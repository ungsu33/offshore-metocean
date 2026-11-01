"""Serializable project settings. Defaults are screening settings, not code requirements."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
import json
import math
from pathlib import Path


VARIABLES = ("wind_speed", "hs", "tp", "wind_dir", "wave_dir")
STATUS_LABELS = {"eligible": "채택 후보", "provisional": "잠정", "withheld": "산정 보류", "demo": "합성 예제"}


@dataclass
class SourceMeta:
    name: str = "사용자 자료"
    provider: str = "User"
    version: str = ""
    kind: str = "hindcast"
    reference: str = ""
    site_assessment: str = ""
    atmosphere_grid_km: float | None = None
    wave_grid_km: float | None = None
    forcing_grid_km: float | None = None
    delivery_grid_km: float | None = None
    integration_minutes: float | None = None
    output_minutes: float | None = None
    wind_statistic: str = "unknown"
    wind_average_minutes: float | None = None
    wind_height_m: float | None = None
    period_definition: str = "unknown"
    direction_convention: str = "unknown"
    timezone: str = "UTC"
    homogeneous: bool = False
    lineage: str = ""
    synthetic: bool = False
    units: dict = field(default_factory=lambda: {
        "wind_speed": "m/s", "hs": "m", "tp": "s", "wind_dir": "deg", "wave_dir": "deg"
    })


@dataclass
class DLCRule:
    identifier: str
    situation: str
    basis: str = "normal"
    code_reference: str = ""
    verified: bool = False


def default_rules():
    return [DLCRule("OP-NORMAL", "정상운전 / 피로"),
            DLCRule("OP-TRANSIENT", "시동·정지", "normal"),
            DLCRule("OP-FAULT", "고장·비상정지", "normal"),
            DLCRule("PARKED-EXTREME", "정지·대기 / 극한", "extreme")]


@dataclass
class Project:
    name: str = "Korea offshore wind · screening"
    demo_mode: bool = False
    latitude: float = 35.5
    longitude: float = 125.8
    hub_height_m: float = 150.0
    wind_average_minutes: float = 10.0
    period_definition: str = "Tp"
    wind_bin_width: float = 2.0
    cut_in: float = 3.0
    cut_out: float = 25.0
    direction_sectors: int = 12
    return_periods: list[float] = field(default_factory=lambda: [2.0, 10.0, 50.0])
    extreme_method: str = "Gumbel"
    min_complete_years: int = 10
    min_annual_coverage: float = 0.8
    min_validation_pairs: int = 30
    max_nrmse: float = 0.3
    max_grid_km: float | None = None
    max_output_minutes: float | None = None
    bootstrap_samples: int = 100
    confidence: float = 0.95
    random_seed: int = 42
    allow_provisional: bool = True
    criteria_confirmed: bool = False
    shear_exponent: float | None = None
    shear_reference: str = ""
    standard_basis: str = "IEC 61400-3-1 + DNV-ST-0437 + DNV-RP-C205 · 판본 미확정"
    rules: list[DLCRule] = field(default_factory=default_rules)
    source_meta: SourceMeta = field(default_factory=SourceMeta)
    primary_files: list[str] = field(default_factory=list)
    comparison_files: list[str] = field(default_factory=list)
    observation_files: list[str] = field(default_factory=list)
    comparison_meta: SourceMeta = field(default_factory=lambda: SourceMeta(name="비교 hindcast"))
    observation_meta: SourceMeta = field(default_factory=lambda: SourceMeta(name="관측", kind="observation"))
    column_map: dict[str, str] = field(default_factory=dict)

    def validate(self):
        numeric = (self.latitude, self.longitude, self.hub_height_m, self.wind_average_minutes,
                   self.wind_bin_width, self.cut_in, self.cut_out, self.max_nrmse, self.confidence)
        if not all(math.isfinite(v) for v in numeric):
            raise ValueError("설정에는 유한한 숫자만 입력할 수 있습니다.")
        if not -90 <= self.latitude <= 90 or not -180 <= self.longitude <= 180:
            raise ValueError("위도는 -90~90°, 경도는 -180~180° 범위여야 합니다.")
        if self.hub_height_m <= 0 or self.wind_average_minutes <= 0 or self.wind_bin_width <= 0:
            raise ValueError("높이·평균시간·풍속 구간 폭은 양수여야 합니다.")
        if not 0 <= self.cut_in < self.cut_out <= 100 or not 1 <= self.direction_sectors <= 72:
            raise ValueError("운전 풍속 또는 방향 분할 설정이 올바르지 않습니다.")
        if not self.return_periods or any(not math.isfinite(t) or t <= 1 for t in self.return_periods):
            raise ValueError("연최대 분포의 재현기간은 1년보다 커야 합니다. 1년 조건은 별도 정의가 필요합니다.")
        if self.extreme_method not in ("Gumbel", "GEV"):
            raise ValueError("지원하는 극치 방법은 Gumbel 또는 GEV입니다.")
        if not 0 < self.min_annual_coverage <= 1 or self.min_complete_years < 3:
            raise ValueError("연간 자료 충족률은 (0,1], 유효 연수는 3년 이상이어야 합니다.")
        if not 0 < self.confidence < 1 or not 20 <= self.bootstrap_samples <= 2000:
            raise ValueError("신뢰수준은 (0,1), bootstrap 횟수는 20~2000이어야 합니다.")
        if self.min_validation_pairs < 3 or self.max_nrmse <= 0:
            raise ValueError("검증 표본 수·오차 한계를 확인하세요.")
        if self.shear_exponent is not None and (not 0 <= self.shear_exponent <= 0.6 or not self.shear_reference):
            raise ValueError("높이 보정에는 0~0.6 범위의 전단 지수와 적용 근거가 필요합니다.")
        ids = [r.identifier for r in self.rules]
        if len(set(ids)) != len(ids) or any(not x.strip() for x in ids):
            raise ValueError("DLC 식별자는 중복 없이 입력하세요.")
        if any(r.verified and not r.code_reference.strip() for r in self.rules):
            raise ValueError("확인된 DLC 규칙에는 기준명·판본·조항을 입력하세요.")

    def to_dict(self):
        return asdict(self)

    def save(self, path):
        self.validate()
        Path(path).write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def from_dict(cls, data):
        allowed = {f.name for f in fields(cls)}
        values = {k: v for k, v in data.items() if k in allowed}
        for key in ("source_meta", "comparison_meta", "observation_meta"):
            if key in values:
                values[key] = SourceMeta(**values[key])
        if "rules" in values:
            values["rules"] = [DLCRule(**r) for r in values["rules"]]
        result = cls(**values)
        result.validate()
        return result

    @classmethod
    def load(cls, path):
        path = Path(path)
        result = cls.from_dict(json.loads(path.read_text(encoding="utf-8-sig")))
        for key in ("primary_files", "comparison_files", "observation_files"):
            setattr(result, key, [str((path.parent / p).resolve()) if not Path(p).is_absolute() else p
                                 for p in getattr(result, key)])
        return result
