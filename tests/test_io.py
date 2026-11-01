from dataclasses import replace
import hashlib
import json

import numpy as np
import pandas as pd
import pytest
import xarray as xr
from openpyxl import load_workbook

from offshore_metocean.analysis import run_analysis
from offshore_metocean.data import canonicalize, load_files
from offshore_metocean.export import export_result
from offshore_metocean.models import Project, SourceMeta
from offshore_metocean.providers import era5_requests


def test_timezone_and_vector_wind():
    raw = pd.DataFrame({"time": ["2020-01-01 09:00", "2020-01-01 12:00"], "u10": [0, -3], "v10": [-4, 0],
                        "swh": [1, 2], "pp1d": [5, 6], "mwd": [90, 180]})
    data = canonicalize(raw, SourceMeta(timezone="Asia/Seoul", direction_convention="to"))
    assert str(data.index[0]) == "2020-01-01 00:00:00+00:00"
    np.testing.assert_allclose(data.wind_speed, [4, 3])
    np.testing.assert_allclose(data.wind_dir, [0, 90])
    np.testing.assert_allclose(data.wave_dir, [270, 0])


def test_duplicate_timestamps_raise():
    with pytest.raises(ValueError, match="중복"):
        canonicalize(pd.DataFrame({"time": ["2020-01-01", "2020-01-01"]}), SourceMeta())


def test_invalid_negative_and_infinite_values_are_missing():
    frame = canonicalize(pd.DataFrame({"time": ["2020-01-01", "2020-01-02"],
                        "hs": [-1, np.inf], "tp": [0, 3]}), SourceMeta())
    assert frame.hs.isna().all()
    assert pd.isna(frame.tp.iloc[0])


def test_csv_column_mapping_and_file_conflict(tmp_path, cfg):
    raw = pd.DataFrame({"utc": ["2020-01-01", "2020-01-02"], "height": [2, 3]})
    first, second = tmp_path/"a.csv", tmp_path/"b.csv"
    raw.to_csv(first, index=False)
    cfg.column_map = {"timestamp": "utc", "hs": "height"}
    data = load_files([first], SourceMeta(), cfg)
    assert data.frame.hs.tolist() == [2, 3]
    raw["height"] = [3, 4]
    raw.to_csv(second, index=False)
    with pytest.raises(ValueError, match="충돌"):
        load_files([first, second], SourceMeta(), cfg)


def test_netcdf_spatial_selection_units_and_separate_files(tmp_path, cfg):
    time = pd.date_range("2020-01-01", periods=3, freq="h")
    cfg.latitude, cfg.longitude = 35.4, 125.9
    ds = xr.Dataset({"swh": (("time", "latitude", "longitude"), np.full((3, 2, 2), 2.))},
                    coords={"time": time, "latitude": [35., 36.], "longitude": [125., 126.]})
    ds.swh.attrs["units"] = "m"
    path = tmp_path/"파랑자료.nc"
    ds.to_netcdf(path, engine="h5netcdf")
    data = load_files([path], SourceMeta(), cfg)
    assert len(data.frame) == 3
    assert data.provenance[0]["selected_longitude"] == 126
    assert data.provenance[0]["distance_km"] > 0
    ds.swh.attrs["units"] = "cm"
    ds.to_netcdf(tmp_path/"bad.nc", engine="h5netcdf")
    with pytest.raises(ValueError, match="단위"):
        load_files([tmp_path/"bad.nc"], SourceMeta(), cfg)


def test_config_roundtrip_and_relative_paths(tmp_path, cfg):
    cfg.primary_files = ["example.csv"]
    cfg.save(tmp_path/"settings.json")
    read = Project.load(tmp_path/"settings.json")
    assert read.hub_height_m == cfg.hub_height_m
    assert read.primary_files == [str((tmp_path/"example.csv").resolve())]


def test_export_report_manifest_and_formula_safety(tmp_path, demo, cfg):
    a, b = demo
    a = replace(a, meta=replace(a.meta, name="=HYPERLINK(\"bad\")"))
    cfg.name = "<script>bad()</script>"
    result = run_analysis(a, cfg, b)
    target = export_result(result, tmp_path)
    manifest = json.loads((target/"manifest.json").read_text(encoding="utf-8"))
    for name, digest in manifest["artifacts_sha256"].items():
        assert hashlib.sha256((target/name).read_bytes()).hexdigest() == digest
    report = (target/"report.html").read_text(encoding="utf-8")
    assert "<script>bad()" not in report
    assert "&lt;script&gt;" in report
    book = load_workbook(target/"environmental_conditions.xlsx")
    assert "Wind_Hs_T_occurrence" in book.sheetnames
    for sheet in book:
        assert not any(cell.data_type == "f" for row in sheet for cell in row)


def test_cds_plan_separates_grids_and_valid_calendar():
    requests = era5_requests(35.5, 125.8, 2020, 2020)
    assert len(requests) == 24
    feb = next(x for x in requests if x["filename"] == "era5_2020_02_wave.nc")
    assert len(feb["request"]["day"]) == 29
    assert "10m_u_component_of_wind" not in feb["request"]["variable"]
    assert "key" not in json.dumps(requests)
