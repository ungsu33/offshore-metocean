from dataclasses import replace
import json
import logging
from threading import Event

import pytest
import xarray as xr

from offshore_metocean.analysis import extreme_estimates
from offshore_metocean.gui import MainWindow, Worker
from offshore_metocean.providers import download_era5, era5_requests
from offshore_metocean.tasks import TaskCancelled
from offshore_metocean import startup


def test_worker_cancellation_does_not_emit_success(qtbot):
    ready = Event()
    def task(progress, checkpoint):
        ready.set()
        while True:
            checkpoint()
            ready.wait(.001)
    worker = Worker(task)
    succeeded, cancelled = [], []
    worker.success.connect(succeeded.append)
    worker.cancelled.connect(lambda: cancelled.append(True))
    worker.start()
    qtbot.waitUntil(ready.is_set)
    worker.cancel()
    qtbot.waitUntil(lambda: not worker.isRunning() and bool(cancelled), timeout=5000)
    assert not succeeded


def test_bootstrap_cancellation_reaches_inner_loop(demo, cfg):
    calls = []
    def checkpoint():
        calls.append(True)
        if len(calls) == 4:
            raise TaskCancelled()
    with pytest.raises(TaskCancelled):
        extreme_estimates(demo[0].frame.hs, 180, cfg, "hs", "demo", checkpoint)
    assert len(calls) == 4


def test_gui_cancel_and_run_again(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    errors = []
    window.show_error = errors.append
    window.load_demo()
    window.bootstrap.setValue(2000)
    window.start_analysis()
    assert window.cancel_button.isEnabled()
    window.request_cancel()
    qtbot.waitUntil(lambda: not window.worker.isRunning() and window.run_button.isEnabled(), timeout=10000)
    assert window.result is None
    assert not window.export_button.isEnabled()
    assert not errors
    assert not window.cancel_button.isEnabled()
    window.bootstrap.setValue(20)
    window.start_analysis()
    qtbot.waitUntil(lambda: window.result is not None and not window.worker.isRunning(), timeout=60000)
    assert window.export_button.isEnabled()
    window.close()


def test_download_does_not_attach_to_modified_site(qtbot):
    window = MainWindow()
    qtbot.addWidget(window)
    revision = window._revision
    window.latitude.setValue(34)
    window.cds_done((revision, ["wrong-site.nc"]))
    assert window.files["primary"] == []
    assert "자동 연결은 보류" in window.status_label.text()


def test_requests_for_distinct_sites_have_distinct_cache_names():
    a = era5_requests(35.5, 125.8, 2020, 2020)
    b = era5_requests(34.5, 125.8, 2020, 2020)
    assert not ({x["filename"] for x in a} & {x["filename"] for x in b})
    assert a == era5_requests(35.5, 125.8, 2020, 2020)


@pytest.fixture
def fake_cds(monkeypatch):
    import cdsapi
    calls = []
    class Client:
        def __init__(self, **kwargs):
            pass
        def retrieve(self, dataset, request, destination):
            calls.append((dataset, request))
            xr.Dataset({"test_variable": ("time", [1., 2.])}, coords={"time": [0, 1]}).to_netcdf(
                destination, engine="h5netcdf")
    monkeypatch.setattr(cdsapi, "Client", Client)
    return calls


def test_download_cache_requires_receipt_matching_request_and_content(tmp_path, fake_cds):
    request = era5_requests(35.5, 125.8, 2020, 2020)[:1]
    files = download_era5(request, tmp_path)
    assert len(fake_cds) == 1
    assert download_era5(request, tmp_path) == files
    assert len(fake_cds) == 1
    record = tmp_path/request[0]["filename"]
    receipt = record.with_suffix(".request.json")
    metadata = json.loads(receipt.read_text())
    metadata["request_sha256"] = "a-different-site"
    receipt.write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="다른 좌표"):
        download_era5(request, tmp_path)


def test_modified_cache_is_rejected(tmp_path, fake_cds):
    request = era5_requests(35.5, 125.8, 2020, 2020)[:1]
    download_era5(request, tmp_path)
    target = tmp_path/request[0]["filename"]
    target.write_bytes(b"modified")
    with pytest.raises(ValueError, match="체크섬"):
        download_era5(request, tmp_path)


def test_cancelled_download_keeps_partial_out_of_final_cache(tmp_path, fake_cds):
    request = era5_requests(35.5, 125.8, 2020, 2020)[:1]
    calls = []
    def checkpoint():
        calls.append(True)
        if len(calls) == 3:
            raise TaskCancelled()
    with pytest.raises(TaskCancelled):
        download_era5(request, tmp_path, checkpoint=checkpoint)
    assert not (tmp_path/request[0]["filename"]).exists()
    assert list(tmp_path.glob("*.partial.nc"))


def test_startup_failure_returns_actionable_message_and_log(tmp_path, monkeypatch):
    monkeypatch.setenv("OFFSHORE_METOCEAN_LOG_DIR", str(tmp_path))
    messages = []
    monkeypatch.setattr(startup, "notify_failure", messages.append)
    def fail(**kwargs):
        raise RuntimeError("test-startup-failure")
    monkeypatch.setattr(startup, "_run_gui", fail)
    assert startup.launch_gui(demo=True) == 1
    assert "app.log" in messages[0]
    assert "test-startup-failure" in (tmp_path/"app.log").read_text(encoding="utf-8")


def test_log_redacts_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("OFFSHORE_METOCEAN_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("CDSAPI_KEY", "unit-test-secret-value")
    path = startup.configure_logging()
    logging.getLogger("offshore_metocean").warning("unit-test-secret-value authKey=another-test-value&x=1")
    text = path.read_text(encoding="utf-8")
    assert "unit-test-secret-value" not in text
    assert "another-test-value" not in text
    assert "[redacted]" in text


def test_cli_gui_startup_options(monkeypatch):
    from offshore_metocean.cli import main
    options = []
    monkeypatch.setattr(startup, "launch_gui", lambda **kwargs: options.append(kwargs) or 0)
    assert main(["gui", "--demo", "--run"]) == 0
    assert options == [{"config_path": None, "demo": True, "auto_run": True}]


def test_gev_option_produces_ordered_finite_screening_levels(demo, cfg):
    cfg = replace(cfg, extreme_method="GEV")
    values, _ = extreme_estimates(demo[0].frame.hs, 180, cfg, "hs", "demo")
    assert values.return_level.notna().all()
    assert values.return_level.is_monotonic_increasing
    assert (values.ci_high >= values.ci_low).all()
