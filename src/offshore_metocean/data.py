"""CSV/NetCDF ingestion; never infer physical resolution or averaging from file spacing."""
from __future__ import annotations

from dataclasses import dataclass, field
from contextlib import contextmanager
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

from .models import SourceMeta, VARIABLES


ALIASES = {
    "timestamp": ["timestamp", "time", "valid_time", "datetime", "date"],
    "wind_speed": ["wind_speed", "ws", "wind", "U", "wspd"],
    "hs": ["hs", "Hs", "swh", "significant_wave_height"],
    "tp": ["tp", "Tp", "pp1d", "peak_wave_period"],
    "wind_dir": ["wind_dir", "wd", "wdir", "wind_direction"],
    "wave_dir": ["wave_dir", "mwd", "mean_wave_direction"],
}


@dataclass
class Dataset:
    frame: pd.DataFrame
    meta: SourceMeta
    provenance: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def haversine(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians([lat1, lat2])
    dp, dl = np.radians([lat2 - lat1, ((lon2 - lon1 + 180) % 360) - 180])
    return float(6371 * 2 * np.arcsin(np.sqrt(np.sin(dp/2)**2 + np.cos(p1)*np.cos(p2)*np.sin(dl/2)**2)))


def canonicalize(frame, meta, column_map=None):
    column_map = column_map or {}
    out = pd.DataFrame()
    for target, aliases in ALIASES.items():
        choices = [column_map[target]] if target in column_map else aliases
        source = next((a for a in choices if a in frame.columns), None)
        if source:
            out[target] = frame[source]
    if "timestamp" not in out:
        raise ValueError("timestamp/time 열이 없습니다. 열 매핑을 확인하세요.")
    vector_direction = "wind_speed" not in out and {"u10", "v10"}.issubset(frame.columns)
    if vector_direction:
        u, v = pd.to_numeric(frame.u10, errors="coerce"), pd.to_numeric(frame.v10, errors="coerce")
        out["wind_speed"] = np.hypot(u, v)
        out["wind_dir"] = (np.degrees(np.arctan2(-u, -v)) + 360) % 360
    if not meta.timezone:
        raise ValueError("자료의 시간대가 필요합니다.")
    times = pd.to_datetime(out.pop("timestamp"), errors="raise", format="mixed")
    if times.dt.tz is None:
        times = times.dt.tz_localize(meta.timezone, ambiguous="raise", nonexistent="raise")
    out.index = pd.DatetimeIndex(times.dt.tz_convert("UTC"), name="timestamp")
    if out.index.has_duplicates:
        raise ValueError("중복 시각이 있습니다. 임의 평균하지 않습니다. 원자료를 확인하세요.")
    for var in VARIABLES:
        if var not in out:
            out[var] = np.nan
        out[var] = pd.to_numeric(out[var], errors="coerce").to_numpy(dtype=float)
        out.loc[~np.isfinite(out[var]), var] = np.nan
        if var in ("wind_speed", "hs", "tp"):
            out.loc[out[var] < 0, var] = np.nan
            if var == "tp":
                out.loc[out[var] == 0, var] = np.nan
        elif meta.direction_convention in ("from", "to"):
            if meta.direction_convention == "to" and not (var == "wind_dir" and vector_direction):
                out[var] = out[var] + 180
            out[var] = out[var] % 360
    return out[list(VARIABLES)].sort_index()


@contextmanager
def open_netcdf(path):
    """Python file handles avoid Windows C-library filename encoding limitations."""
    import xarray as xr
    with Path(path).open("rb") as stream:
        magic = stream.read(8)
        stream.seek(0)
        engine = "h5netcdf" if magic.startswith(b"\x89HDF") else "scipy"
        with xr.open_dataset(stream, engine=engine) as ds:
            yield ds


def read_netcdf(path, project):
    notes = {}
    with open_netcdf(path) as original:
        ds = original
        lat = next((k for k in ("latitude", "lat") if k in ds.coords), None)
        lon = next((k for k in ("longitude", "lon") if k in ds.coords), None)
        if lat and lon:
            if ds[lat].ndim != 1 or ds[lon].ndim != 1:
                raise ValueError("비정규/곡선 격자는 해상점 추출 후 CSV로 가져오세요. 임의 보간하지 않습니다.")
            target_lon = project.longitude % 360 if float(ds[lon].max()) > 180 else project.longitude
            ds = ds.sel({lat: project.latitude, lon: target_lon}, method="nearest")
            notes = {"selected_latitude": float(ds[lat]), "selected_longitude": float(ds[lon]),
                     "distance_km": haversine(project.latitude, project.longitude, float(ds[lat]), float(ds[lon]))}
        if "expver" in ds.dims:
            raise ValueError("NetCDF의 expver 혼합 자료입니다. ERA5/ERA5T를 구분한 파일을 사용하세요.")
        time = next((k for k in ("valid_time", "time") if k in ds.coords), None)
        if not time:
            raise ValueError("NetCDF에 time/valid_time 좌표가 없습니다.")
        recognized = {a for names in ALIASES.values() for a in names} | {"u10", "v10"} | set(project.column_map.values())
        columns = {}
        units = {}
        for name in ds.data_vars:
            if name in recognized:
                arr = ds[name].squeeze(drop=True)
                if arr.dims != (time,):
                    raise ValueError(f"{name}: 시간 외 추가 차원이 있습니다. ensemble/level을 먼저 선택하세요.")
                columns[name] = arr.values
                units[name] = arr.attrs.get("units", "")
        if not columns:
            raise ValueError("지원 변수(u10/v10/swh/pp1d/mwd 등)가 없습니다. 변수명을 매핑하세요.")
        columns["timestamp"] = ds[time].values
        notes["variable_units"] = units
        return pd.DataFrame(columns), notes


def load_files(paths, meta, project):
    if not paths:
        raise ValueError("분석할 CSV 또는 NetCDF 파일을 선택하세요.")
    frames, evidence = [], []
    for filename in paths:
        path = Path(filename)
        if path.suffix.lower() in (".nc", ".nc4", ".netcdf"):
            raw, selection = read_netcdf(path, project)
            accepted_units = {"m/s": {"m/s", "m s-1", "m s**-1", "m s^-1"},
                              "m": {"m", "metres", "meters"}, "s": {"s", "seconds"},
                              "deg": {"deg", "degree", "degrees", "degree_true", "degrees_true"}}
            for var, names in ALIASES.items():
                if var == "timestamp":
                    continue
                names = [project.column_map[var]] if var in project.column_map else names
                for name in names + (["u10", "v10"] if var == "wind_speed" else []):
                    actual_unit = selection["variable_units"].get(name, "")
                    if actual_unit and actual_unit not in accepted_units.get(meta.units.get(var), set()):
                        raise ValueError(f"{name}: NetCDF 단위 '{actual_unit}'와 선언 단위 '{meta.units.get(var)}'가 다릅니다.")
        else:
            raw = pd.read_csv(path, encoding="utf-8-sig")
            selection = {}
        frames.append(canonicalize(raw, meta, project.column_map))
        evidence.append({"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), **selection})
    combined = frames[0]
    for frame in frames[1:]:
        overlap = combined.index.intersection(frame.index)
        for var in VARIABLES:
            a, b = combined.loc[overlap, var], frame.loc[overlap, var]
            good = a.notna() & b.notna()
            if good.any() and not np.allclose(a[good], b[good], equal_nan=True):
                raise ValueError(f"파일 사이에 {var} 값이 충돌합니다. 비교 자료는 별도로 불러오세요.")
        combined = combined.combine_first(frame)
    if len(combined) < 2:
        raise ValueError("2개 이상의 서로 다른 시각이 필요합니다.")
    return Dataset(combined.sort_index(), meta, evidence)


def demo_datasets():
    """Deterministic synthetic teaching data, NEVER a Korean site measurement/hindcast."""
    rng = np.random.default_rng(713)
    idx = pd.date_range("2000-01-01", "2020-01-01", freq="3h", inclusive="left", tz="UTC")
    n = len(idx)
    seasonal = 1.8 * np.cos(np.arange(n) / (365.25 * 8) * 2*np.pi)
    smooth = np.convolve(rng.normal(0, 1, n+30), np.ones(16)/4, mode="valid")[:n]
    wind = np.maximum(0.2, 8 + seasonal + 2.0*smooth + rng.weibull(2.1, n)*2)
    hs = np.maximum(.1, .25 + .025 * wind**1.65 + rng.gamma(1.8, .25, n))
    tp = np.maximum(2., 3.3 + 1.6*np.sqrt(hs) + rng.normal(0, .6, n))
    wd = (310 + np.sin(np.arange(n)/160)*70 + rng.normal(0, 24, n)) % 360
    frame = pd.DataFrame({"wind_speed": wind, "hs": hs, "tp": tp, "wind_dir": wd,
                          "wave_dir": (wd+25+rng.normal(0, 18, n)) % 360}, index=idx)
    frame.index.name = "timestamp"
    meta = SourceMeta(name="SYNTHETIC A · 실측 아님", provider="Generated demo", version="seed713-v1",
                      atmosphere_grid_km=15, wave_grid_km=5, forcing_grid_km=15,
                      delivery_grid_km=5, output_minutes=180, wind_statistic="mean",
                      wind_average_minutes=10, wind_height_m=150, period_definition="Tp",
                      direction_convention="from", homogeneous=True, synthetic=True,
                      reference="Synthetic demonstration; no physical site applicability", lineage="synthetic713")
    from dataclasses import replace
    other = frame.copy()
    for key, scale in (("wind_speed", .5), ("hs", .12), ("tp", .2)):
        other[key] = np.maximum(.01, other[key] + rng.normal(0, scale, n))
    other["wind_dir"] = (other.wind_dir + 3) % 360
    other["wave_dir"] = (other.wave_dir + 4) % 360
    return Dataset(frame, meta, [{"file": "generated-demo", "seed": 713}]), Dataset(
        other, replace(meta, name="SYNTHETIC B · 비교용", lineage="synthetic713-shared"))
