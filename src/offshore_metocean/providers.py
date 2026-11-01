"""Documented source catalogue and explicit, monthly CDS requests."""
from __future__ import annotations

import calendar
import json
from pathlib import Path

from .models import SourceMeta


CATALOG = [
    {"name": "ECMWF / Copernicus ERA5", "type": "Global reanalysis",
     "native_grid": "Atmosphere ~31 km; waves native 0.36° (separate delivery grid)",
     "time": "Hourly output; inspect variable statistics, NOT an hourly-mean declaration",
     "url": "https://confluence.ecmwf.int/spaces/CKB/pages/76414402/ERA5%2Bdata%2Bdocumentation",
     "access": "CDS API: 개인 토큰·데이터셋 이용약관 동의 필요"},
    {"name": "기상청 API허브 / 해양관측", "type": "Observation / product-specific model data",
     "native_grid": "모델 제품별 확인. 관측은 격자 대신 지점·관측 높이·대표성 확인",
     "time": "관측항목·장비별 평균시간/제공 간격 확인",
     "url": "https://apihub.kma.go.kr/apiInfo.do",
     "access": "기상청에서 내려받은 CSV를 열 매핑·메타데이터와 함께 불러오기"},
    {"name": "NOAA / NCEP WAVEWATCH III", "type": "Hindcast / operational archive",
     "native_grid": "원래 파랑 격자·바람 강제력·제품 버전별 확인",
     "time": "제품별 확인. production hindcast의 모델 변경 이력 확인 필수",
     "url": "https://www.emc.ncep.noaa.gov/emc/pages/numerical_forecast_systems/wavemodels.php",
     "access": "공식 아카이브에서 확보한 NetCDF/CSV 불러오기"},
]


def era5_metadata():
    return SourceMeta(name="ERA5 · imported monthly files", provider="ECMWF/C3S", version="IFS CY41R2",
                      reference=CATALOG[0]["url"], atmosphere_grid_km=31, wave_grid_km=40.1,
                      forcing_grid_km=31, output_minutes=60, wind_statistic="instantaneous",
                      wind_height_m=10, period_definition="Tp", direction_convention="from",
                      homogeneous=True, lineage="ERA5-IFS-CY41R2",
                      # 40.1 km is the approximate meridional scale of 0.36°; see documentation.
                      delivery_grid_km=None)


def era5_requests(latitude, longitude, first_year, last_year):
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise ValueError("좌표 범위가 올바르지 않습니다.")
    if not 1940 <= first_year <= last_year <= 2100:
        raise ValueError("연도 범위를 확인하세요. 최신 공개 연도는 CDS에서 확인해야 합니다.")
    # Separate requests retain atmosphere/wave grid identity and avoid mixed-grid files.
    groups = {
        "atmosphere": ["10m_u_component_of_wind", "10m_v_component_of_wind"],
        "wave": ["significant_height_of_combined_wind_waves_and_swell", "peak_wave_period", "mean_wave_direction"],
    }
    north, south = min(90, latitude+.5), max(-90, latitude-.5)
    west, east = max(-180, longitude-.5), min(180, longitude+.5)
    requests = []
    for year in range(first_year, last_year+1):
        for month in range(1, 13):
            for group, variables in groups.items():
                request = {"product_type": ["reanalysis"], "variable": variables,
                    "year": [str(year)], "month": [f"{month:02d}"],
                    "day": [f"{d:02d}" for d in range(1, calendar.monthrange(year, month)[1]+1)],
                    "time": [f"{h:02d}:00" for h in range(24)],
                    "data_format": "netcdf", "download_format": "unarchived",
                    "area": [north, west, south, east]}
                requests.append({"dataset": "reanalysis-era5-single-levels", "request": request,
                                 "filename": f"era5_{year}_{month:02d}_{group}.nc"})
    return requests


def save_request_plan(requests, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "era5_request_plan.json"
    path.write_text(json.dumps(requests, indent=2), encoding="utf-8")
    return path


def download_era5(requests, directory, progress=None):
    import cdsapi
    from .data import open_netcdf
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    save_request_plan(requests, directory)
    # Uses the user's existing .cdsapirc/CDSAPI_URL/CDSAPI_KEY; never persist keys in a project.
    client = cdsapi.Client(quiet=True, progress=False, timeout=120)
    outputs = []
    for i, item in enumerate(requests):
        if progress:
            progress(int(i/len(requests)*100), f"CDS {i+1}/{len(requests)} · {item['filename']}")
        target = directory / item["filename"]
        # Do not silently treat a partial/corrupt cached file as valid.
        if target.exists():
            with open_netcdf(target) as ds:
                if not ds.data_vars:
                    raise ValueError(f"캐시 파일에 변수가 없습니다: {target.name}")
        else:
            temporary = target.with_suffix(".partial.nc")
            client.retrieve(item["dataset"], item["request"], str(temporary))
            with open_netcdf(temporary) as ds:
                if not ds.data_vars:
                    raise ValueError("CDS 응답이 유효한 NetCDF가 아닙니다.")
            temporary.replace(target)
        outputs.append(str(target))
    if progress:
        progress(100, "ERA5 다운로드 완료 · 원래 시간/격자 정의를 확인하세요")
    return outputs
