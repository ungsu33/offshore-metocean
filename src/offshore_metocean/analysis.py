"""Evidence-gated screening statistics; no claim of certified DLC completeness."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import warnings

import numpy as np
import pandas as pd
from scipy import stats

from .data import Dataset
from .models import Project, SourceMeta, VARIABLES


@dataclass
class Result:
    project: Project
    primary: Dataset
    frames: dict[str, pd.DataFrame]
    notes: list[str] = field(default_factory=list)
    sources: dict = field(default_factory=dict)


def circular_difference(a, b):
    return (np.asarray(a) - np.asarray(b) + 180) % 360 - 180


def prepare(dataset, cfg):
    frame = dataset.frame.copy()
    meta = dataset.meta
    if (meta.wind_height_m and meta.wind_height_m > 0 and cfg.shear_exponent is not None
            and cfg.shear_reference):
        frame["wind_speed"] *= (cfg.hub_height_m / meta.wind_height_m) ** cfg.shear_exponent
    return frame


def metadata_issues(meta: SourceMeta, var: str, cfg: Project):
    problems = []
    expected = {"wind_speed": "m/s", "hs": "m", "tp": "s", "wind_dir": "deg", "wave_dir": "deg"}
    if meta.units.get(var) != expected[var]:
        problems.append(f"단위 {expected[var]} 확인 필요")
    if not meta.reference.strip():
        problems.append("출처/변수 정의 문서 누락")
    if not meta.synthetic and not meta.site_assessment.strip():
        problems.append("지점 대표성 검토 근거 누락: 위치·수심·차폐·비교 지점 등을 확인")
    if not meta.output_minutes or not np.isfinite(meta.output_minutes) or meta.output_minutes <= 0:
        problems.append("제공 간격 미확인")
    if meta.kind != "observation":
        if not meta.homogeneous:
            problems.append("장기 모델·강제력 변경 이력 미확인")
        grids = [meta.atmosphere_grid_km] if var in ("wind_speed", "wind_dir") else [meta.wave_grid_km, meta.forcing_grid_km]
        if any(g is None or not np.isfinite(g) or g <= 0 for g in grids):
            problems.append("원래 모델/바람 강제력 격자 미확인")
        elif cfg.max_grid_km and max(grids) > cfg.max_grid_km:
            problems.append("원래 모델/강제력 격자가 프로젝트 상한 초과")
    if cfg.max_output_minutes and meta.output_minutes and meta.output_minutes > cfg.max_output_minutes:
        problems.append("제공 간격이 프로젝트 상한 초과")
    if var == "wind_speed":
        if meta.wind_statistic != "mean":
            problems.append("순간값/미확인 풍속: 목표 평균풍속으로 자동 변환 불가")
        if meta.wind_average_minutes is None or not np.isclose(meta.wind_average_minutes, cfg.wind_average_minutes):
            problems.append("원자료·설계 풍속 평균시간 불일치/미확인")
        if not meta.wind_height_m or meta.wind_height_m <= 0:
            problems.append("풍속 기준 높이 미확인")
        elif not np.isclose(meta.wind_height_m, cfg.hub_height_m) and cfg.shear_exponent is None:
            problems.append("목표 높이와 불일치: 근거 있는 높이 보정 필요")
    if var == "tp" and meta.period_definition != cfg.period_definition:
        problems.append("원자료·목표 파주기 정의 불일치/미확인")
    if var in ("wind_dir", "wave_dir") and meta.direction_convention not in ("from", "to"):
        problems.append("방향 from/to 정의 미확인")
    return problems


def compare(primary, reference, cfg, role):
    rows = []
    if reference is None:
        return rows
    a, b = prepare(primary, cfg), prepare(reference, cfg)
    common = a.index.intersection(b.index)
    for var in VARIABLES:
        issues = metadata_issues(primary.meta, var, cfg) + metadata_issues(reference.meta, var, cfg)
        aligned = pd.concat([a.loc[common, var].rename("a"), b.loc[common, var].rename("b")], axis=1).dropna()
        n = len(aligned)
        delta = (circular_difference(aligned.a, aligned.b) if var.endswith("dir")
                 else (aligned.a-aligned.b).to_numpy())
        rmse = float(np.sqrt(np.mean(delta**2))) if n else None
        bias = (float(np.degrees(np.angle(np.mean(np.exp(1j*np.radians(delta))))))
                if n and var.endswith("dir") else float(np.mean(delta)) if n else None)
        denominator = 180. if var.endswith("dir") else float(np.abs(aligned.b).mean()) if n else 0
        nrmse = rmse/denominator if denominator > 1e-10 and rmse is not None else None
        correlation = None
        if n > 2 and not var.endswith("dir") and aligned.a.std() > 0 and aligned.b.std() > 0:
            correlation = float(aligned.a.corr(aligned.b))
        passed = bool(n >= cfg.min_validation_pairs and not issues and nrmse is not None
                      and nrmse <= cfg.max_nrmse)
        if primary.meta.synthetic != reference.meta.synthetic:
            passed = False
            issues.append("합성 자료와 실제 자료의 비교는 검증 근거가 아님")
        if role == "observation" and reference.meta.kind != "observation":
            passed = False
            issues.append("관측 역할의 자료가 observation으로 선언되지 않음")
        rows.append({"role": role, "reference": reference.meta.name, "variable": var,
                     "pairs": n, "bias": bias, "rmse": rmse, "nrmse": nrmse,
                     "correlation": correlation, "screening_pass": passed,
                     "limitations": "; ".join(issues), "reference_lineage": reference.meta.lineage})
    return rows


def assess(primary, cfg, validation):
    rows = []
    frame = primary.frame
    intervals = np.diff(frame.index.as_unit("ns").asi8)/60e9
    actual = float(np.median(intervals)) if len(intervals) else None
    for var in VARIABLES:
        problems = metadata_issues(primary.meta, var, cfg)
        count = int(frame[var].notna().sum())
        if count < cfg.min_validation_pairs:
            problems.append("유효 자료 수 부족")
        v = validation[validation.variable == var] if not validation.empty else pd.DataFrame()
        obs = bool(v.loc[v.role == "observation", "screening_pass"].any()) if len(v) else False
        cross = bool(v.loc[v.role == "hindcast", "screening_pass"].any()) if len(v) else False
        if primary.meta.output_minutes and len(intervals) and np.any(intervals < primary.meta.output_minutes*.999):
            problems.append("실제 시각 간격이 선언 제공 간격보다 짧음: 메타데이터 확인 필요")
        if problems:
            status = "withheld"
        elif primary.meta.synthetic:
            status = "demo"
        elif obs and cfg.criteria_confirmed:
            status = "eligible"
        elif (obs or cross) and cfg.allow_provisional:
            status = "provisional"
            problems.append("개념·기본설계용 잠정값; 상세설계·인증용 채택 보류")
        else:
            status = "withheld"
            problems.append("관측 검증 또는 충분한 복수 hindcast 비교 근거 부족")
        if not cfg.criteria_confirmed:
            problems.append("현재 수치 기준은 사용자가 검토·확정하지 않은 screening 설정")
        rows.append({"variable": var, "status": status, "valid_samples": count,
                     "output_minutes_declared": primary.meta.output_minutes,
                     "observed_median_spacing_minutes": actual,
                     "atmosphere_native_km": primary.meta.atmosphere_grid_km,
                     "wave_native_km": primary.meta.wave_grid_km,
                     "wind_forcing_km": primary.meta.forcing_grid_km,
                     "delivery_grid_km": primary.meta.delivery_grid_km,
                     "wind_average_minutes": primary.meta.wind_average_minutes if var == "wind_speed" else None,
                     "reason": "; ".join(problems)})
    return pd.DataFrame(rows)


def annual_maxima(series, interval_minutes, min_coverage):
    rows = []
    if not interval_minutes or not np.isfinite(interval_minutes) or interval_minutes <= 0 or series.empty:
        return pd.DataFrame(columns=["year", "count", "coverage", "maximum", "included"])
    for year in range(series.index.min().year, series.index.max().year+1):
        group = series[series.index.year == year]
        start = pd.Timestamp(f"{year}-01-01", tz="UTC")
        end = pd.Timestamp(f"{year+1}-01-01", tz="UTC")
        expected = (end-start).total_seconds()/60/interval_minutes
        valid = group.dropna()
        coverage = len(valid)/expected
        rows.append({"year": int(year), "count": len(valid), "coverage": min(1., coverage),
                     "maximum": float(valid.max()) if len(valid) else None,
                     "included": bool(coverage >= min_coverage)})
    return pd.DataFrame(rows)


def extreme_estimates(series, interval, cfg, variable, data_status, checkpoint=None):
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    annual = annual_maxima(series, interval, cfg.min_annual_coverage)
    annual.insert(0, "variable", variable)
    maxima = annual.loc[annual.included, "maximum"].dropna().to_numpy(dtype=float)
    reason = ""
    if data_status == "withheld":
        reason = "자료 적합성 미충족"
    elif len(maxima) < cfg.min_complete_years:
        reason = f"충족 연수 {len(maxima)} < 설정 최소 {cfg.min_complete_years}"
    elif np.std(maxima) < 1e-8:
        reason = "연최대 값의 분산 부족"
    rows = []
    periods = np.asarray(sorted(set(cfg.return_periods)))
    dist = stats.gumbel_r if cfg.extreme_method == "Gumbel" else stats.genextreme
    params, estimates, boot = None, None, []
    if not reason:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                params = dist.fit(maxima)
                estimates = dist.isf(1/periods, *params)
                if not np.isfinite(estimates).all() or params[-1] <= 0:
                    raise ValueError("유한하지 않은 적합 결과")
                rng = np.random.default_rng(cfg.random_seed + (0 if variable == "wind_speed" else 1))
                for _ in range(cfg.bootstrap_samples):
                    checkpoint()
                    sample = rng.choice(maxima, len(maxima), replace=True)
                    if np.std(sample) < 1e-8:
                        continue
                    try:
                        fitted = dist.fit(sample)
                        predicted = dist.isf(1/periods, *fitted)
                        if np.isfinite(predicted).all() and fitted[-1] > 0:
                            boot.append(predicted)
                    except (ValueError, RuntimeError, FloatingPointError):
                        continue
            if len(boot) < max(20, cfg.bootstrap_samples*.8):
                reason = "bootstrap 적합의 안정성 부족"
        except (ValueError, RuntimeError, FloatingPointError) as exc:
            reason = f"극치 적합 실패: {exc}"
    q = (1-cfg.confidence)/2
    bounds = np.quantile(boot, [q, 1-q], axis=0) if boot and not reason else None
    for i, period in enumerate(periods):
        rows.append({"variable": variable, "return_period_years": float(period),
                     "return_level": float(estimates[i]) if estimates is not None and not reason else None,
                     "ci_low": float(bounds[0, i]) if bounds is not None else None,
                     "ci_high": float(bounds[1, i]) if bounds is not None else None,
                     "method": cfg.extreme_method, "complete_years": len(maxima),
                     "status": "withheld" if reason else data_status, "reason": reason,
                     "parameters": str(tuple(float(p) for p in params)) if params is not None else "",
                     "bootstrap_success": len(boot), "confidence": cfg.confidence,
                     "probability_definition": "P(annual maximum > level) = 1/T; marginal, not joint"})
    return pd.DataFrame(rows), annual


def conditional_statistics(frame, cfg, allowed):
    columns = ["wind_low", "wind_high", "count", "occurrence", "wind_mean", "hs_p50", "hs_p90",
               "tp_p10", "tp_p50", "tp_p90", "sample_status"]
    if not all(allowed.get(v, False) for v in ("wind_speed", "hs", "tp")):
        return pd.DataFrame(columns=columns), pd.DataFrame(), pd.DataFrame()
    joint = frame[["wind_speed", "hs", "tp"]].dropna()
    if joint.empty:
        return pd.DataFrame(columns=columns), pd.DataFrame(), pd.DataFrame()
    bins = np.arange(0, max(cfg.cut_out, float(joint.wind_speed.max()))+cfg.wind_bin_width*1.01,
                     cfg.wind_bin_width)
    groups = joint.groupby(pd.cut(joint.wind_speed, bins, right=False), observed=True)
    rows = []
    for interval, g in groups:
        rows.append(dict(zip(columns, [float(interval.left), float(interval.right), len(g), len(g)/len(joint),
            float(g.wind_speed.mean()), float(g.hs.quantile(.5)), float(g.hs.quantile(.9)),
            float(g.tp.quantile(.1)), float(g.tp.quantile(.5)), float(g.tp.quantile(.9)),
            "sufficient" if len(g) >= cfg.min_validation_pairs else "sparse"])))
    scatter = pd.crosstab(pd.cut(joint.wind_speed, bins, right=False),
                         pd.cut(joint.hs, np.arange(0, joint.hs.max()+.5, .5), right=False),
                         normalize="all")
    scatter.index = scatter.index.astype(str)
    scatter.columns = scatter.columns.astype(str)
    directions = pd.DataFrame()
    if allowed.get("wind_dir") and allowed.get("wave_dir"):
        both = frame[["wind_dir", "wave_dir"]].dropna()
        if len(both):
            width = 360/cfg.direction_sectors
            wind = (((both.wind_dir+width/2) % 360)//width * width).astype(float)
            wave = (((both.wave_dir+width/2) % 360)//width * width).astype(float)
            directions = pd.crosstab(wind.rename("wind_from_deg"), wave.rename("wave_from_deg"), normalize="all")
    return pd.DataFrame(rows, columns=columns), scatter, directions


def combined_status(statuses):
    for state in ("withheld", "demo", "provisional", "eligible"):
        if state in statuses:
            return state
    return "withheld"


def joint_occurrence(frame, cfg, allowed):
    if not all(allowed.get(v) for v in ("wind_speed", "hs", "tp")):
        return pd.DataFrame()
    df = frame[["wind_speed", "hs", "tp"]].dropna()
    if df.empty:
        return pd.DataFrame()
    cuts = {}
    for variable, width in (("wind_speed", cfg.wind_bin_width), ("hs", .5), ("tp", 1.)):
        edges = np.arange(0, df[variable].max()+width*1.01, width)
        cuts[variable+"_bin"] = pd.cut(df[variable], edges, right=False)
    grouped = pd.DataFrame(cuts).groupby(list(cuts), observed=True).size().rename("count").reset_index()
    grouped["p_joint"] = grouped["count"]/len(df)
    grouped["p_given_wind"] = grouped["count"]/grouped.groupby("wind_speed_bin", observed=True)["count"].transform("sum")
    for column in cuts:
        grouped[column] = grouped[column].astype(str)
    return grouped


def dlc_candidates(frame, conditional, extremes, quality, cfg):
    qstatus = dict(zip(quality.variable, quality.status))
    common_status = combined_status([qstatus[v] for v in VARIABLES])
    rows = []

    def add(rule, family, wind=None, hs=None, tp=None, period=None, occurrence=None, extra="", group=None):
        mapping = bool(rule.verified and rule.code_reference)
        status = common_status
        reasons = []
        if not mapping:
            reasons.append("기준·판본·조항 매핑 미확인")
        if status == "withheld":
            wind = hs = tp = None
            reasons.append("종속 변수 자료 적합성 부족")
        if extra:
            reasons.append(extra)
        directions = {}
        for var in ("wind_dir", "wave_dir"):
            values = group[var].dropna() if group is not None and status != "withheld" else pd.Series(dtype=float)
            directions[var] = float(np.degrees(np.angle(np.mean(np.exp(1j*np.radians(values)))))) % 360 if len(values) else None
        rows.append({"scenario_id": f"{rule.identifier}-{len(rows)+1:04d}", "dlc_id": rule.identifier,
            "situation": rule.situation, "family": family,
            "wind_candidate_m_s": wind, "hs_candidate_m": hs, "period_candidate_s": tp,
            "wind_dir_candidate_deg": directions["wind_dir"], "wave_dir_candidate_deg": directions["wave_dir"],
            "marginal_return_years": period, "occurrence": occurrence,
            "data_status": status, "mapping_status": "confirmed_by_user" if mapping else "unverified",
            "review_status": "unreviewed", "code_reference": rule.code_reference,
            "adopted_wind_m_s": None, "adopted_hs_m": None, "adopted_period_s": None,
            "adopted_wind_dir_deg": None, "adopted_wave_dir_deg": None,
            "reason": "; ".join(reasons)})

    for rule in cfg.rules:
        if rule.basis == "normal":
            usable = conditional[(conditional.wind_high > cfg.cut_in) & (conditional.wind_low < cfg.cut_out)]
            if usable.empty:
                add(rule, "normal", extra="풍속·파고·주기 공통 자료 부족")
            for row in usable.itertuples():
                group = frame[(frame.wind_speed >= max(cfg.cut_in, row.wind_low)) &
                              (frame.wind_speed < min(cfg.cut_out, row.wind_high))].dropna(subset=["wind_speed", "hs", "tp"])
                if len(group) < cfg.min_validation_pairs:
                    add(rule, "normal", extra="풍속 구간 공통 표본 부족")
                    continue
                add(rule, "normal_p50", float(group.wind_speed.mean()), float(group.hs.median()), float(group.tp.median()),
                    occurrence=len(group)/len(frame.dropna(subset=["wind_speed", "hs", "tp"])), group=group,
                    extra="주기·방향은 구간의 경험적 대표값; 피로는 전체 결합분포 참조")
        elif rule.basis == "extreme":
            valid = extremes[extremes.return_level.notna()]
            for value in valid.itertuples():
                if value.variable not in ("wind_speed", "hs"):
                    continue
                group = frame.dropna(subset=["wind_speed", "hs", "tp"])
                if len(group):
                    tail = group[group[value.variable] >= group[value.variable].quantile(.95)]
                    wind = float(tail.wind_speed.median())
                    hs = float(tail.hs.median())
                    tp = float(tail.tp.median())
                    if value.variable == "wind_speed":
                        wind = value.return_level
                    else:
                        hs = value.return_level
                    add(rule, f"marginal_{value.variable}", wind, hs, tp, value.return_period_years,
                        group=tail,
                        extra="개별 극값 시나리오 + 관측기간 상위 5% 동반조건. 공동 T년·응답 지배조건 아님")
            if valid.empty:
                add(rule, "extreme", extra="극치 적합/유효 연수 부족")
        else:
            add(rule, "external", extra="외부 환경모델/터빈 정보 필요")
    return pd.DataFrame(rows)


def apply_review(result, indices, decision):
    if decision not in ("accepted_preliminary", "rejected", "unreviewed"):
        raise ValueError("알 수 없는 검토 상태입니다.")
    df = result.frames["DLC_candidates"]
    for index in indices:
        row = df.loc[index]
        candidate_columns = ["wind_candidate_m_s", "hs_candidate_m", "period_candidate_s",
                             "wind_dir_candidate_deg", "wave_dir_candidate_deg"]
        if decision == "accepted_preliminary" and (row.data_status in ("withheld", "demo")
                or row.mapping_status != "confirmed_by_user" or row[candidate_columns].isna().any()):
            raise ValueError("합성 예제·보류·DLC 매핑 미확인 조건은 설계값으로 채택할 수 없습니다.")
    for index in indices:
        df.loc[index, "review_status"] = decision
        for var in ("wind", "hs", "period"):
            unit = "m_s" if var == "wind" else "m" if var == "hs" else "s"
            df.loc[index, f"adopted_{var}_{unit}"] = (
                df.loc[index, f"{var}_candidate_{unit}"] if decision == "accepted_preliminary" else None)
        for var in ("wind_dir", "wave_dir"):
            df.loc[index, f"adopted_{var}_deg"] = (
                df.loc[index, f"{var}_candidate_deg"] if decision == "accepted_preliminary" else None)


def run_analysis(primary, cfg, comparison=None, observation=None, progress=None, checkpoint=None):
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    cfg.validate()
    progress = progress or (lambda value, message: None)
    progress(10, "자료 정의와 비교 가능성 점검")
    validation = pd.DataFrame(compare(primary, observation, cfg, "observation") +
                              compare(primary, comparison, cfg, "hindcast"))
    if validation.empty:
        validation = pd.DataFrame(columns=["role", "variable", "screening_pass", "pairs", "limitations"])
    quality = assess(primary, cfg, validation)
    prepared = prepare(primary, cfg)
    statuses = dict(zip(quality.variable, quality.status))
    allowed = {v: statuses[v] != "withheld" for v in VARIABLES}
    progress(30, "조건부·결합·방향별 분포 산정")
    conditional, scatter, directions = conditional_statistics(prepared, cfg, allowed)
    all_extreme, all_annual = [], []
    for i, variable in enumerate(("wind_speed", "hs")):
        progress(45+i*20, f"{variable} 연최대 {cfg.extreme_method} 적합과 bootstrap")
        extreme, annual = extreme_estimates(prepared[variable], primary.meta.output_minutes,
                                             cfg, variable, statuses[variable], checkpoint)
        all_extreme.append(extreme)
        all_annual.append(annual)
    extremes = pd.concat(all_extreme, ignore_index=True)
    progress(85, "DLC 환경조건 후보 및 근거 연결")
    checkpoint()
    dlc = dlc_candidates(prepared, conditional, extremes, quality, cfg)
    summary = primary.frame.describe(percentiles=[.5, .9, .95, .99]).T.reset_index(names="variable")
    summary["scope"] = "raw-source descriptive statistics; not design values"
    frames = {"DLC_candidates": dlc, "Data_quality": quality, "Validation": validation,
              "Raw_summary": summary, "Conditional": conditional, "Wind_Hs_occurrence": scatter,
              "Direction_occurrence": directions, "Marginal_extremes": extremes,
              "Annual_maxima": pd.concat(all_annual, ignore_index=True)}
    frames["Wind_Hs_T_occurrence"] = joint_occurrence(prepared, cfg, allowed)
    sources = {role: data for role, data in {"primary": primary, "comparison": comparison, "observation": observation}.items() if data is not None}
    frames["Source_catalog"] = pd.DataFrame([{"role": role, **asdict(data.meta),
        "start_utc": str(data.frame.index.min()), "end_utc": str(data.frame.index.max()),
        "record_count": len(data.frame), "files": str(data.provenance)} for role, data in sources.items()])
    frames["Source_catalog"]["units"] = frames["Source_catalog"]["units"].astype(str)
    notes = ["초기 수치 설정은 프로젝트 검토 전 screening 값이며 설계기준의 고정 요구값이 아닙니다.",
             "연최대 적합은 정상성 가정의 개별 변수 후보입니다. 태풍/비태풍 분리·기후 추세는 자동 모델링하지 않습니다.",
             "극한 조합의 동반 주기는 상위 경험표본의 대표값이며 극한영역 조건부 외삽이나 환경등고선이 아닙니다.",
             "DLC의 난류·돌풍·터빈/OEM 입력, 조류·수위, 지반·구조 응답은 별도 검토가 필요합니다."]
    if primary.meta.synthetic:
        notes.insert(0, "합성 예제: 실제 한국 해역의 관측 또는 hindcast가 아닙니다. 설계 사용 불가.")
    if comparison and primary.meta.lineage and primary.meta.lineage == comparison.meta.lineage:
        notes.append("주자료와 비교자료의 생성 계보가 같습니다. 독립적인 관측 검증으로 간주하지 않습니다.")
    if observation is None:
        notes.append("비교 가능한 관측 검증 없음. 허용된 값도 개념·기본설계용 잠정값에 한정됩니다.")
    if cfg.shear_exponent is not None:
        notes.append(f"풍속 높이 보정: power law alpha={cfg.shear_exponent}; 근거={cfg.shear_reference}; 평균시간 보정은 수행하지 않음.")
    progress(100, "분석 완료")
    return Result(cfg, primary, frames, notes, sources)
