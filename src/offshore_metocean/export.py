"""Reviewable tables, escaped HTML report and reproducibility manifest."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import html
import io
import json
from pathlib import Path
import re

import numpy as np
import pandas as pd

from . import __version__


def safe_cell(value):
    # Spreadsheet injection protection for user-provided references, IDs, and file names.
    if isinstance(value, str) and re.match(r"^[\s]*[=+@-]", value):
        return "'"+value
    return value


def table_for_export(frame):
    return frame.map(safe_cell)


def _figure_data(result):
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    import base64
    figure = Figure(figsize=(10, 3.5), facecolor="#fff", layout="constrained")
    FigureCanvasAgg(figure)
    ax1, ax2 = figure.subplots(1, 2)
    raw = result.primary.frame.dropna(subset=["wind_speed", "hs"])
    sample = raw.iloc[::max(1, len(raw)//4000)]
    ax1.scatter(sample.wind_speed, sample.hs, s=4, alpha=.25, color="#138b8a")
    ax1.set(xlabel="Source wind speed (m/s)", ylabel="Hs (m)", title="Raw wind-wave pairs")
    extremes = result.frames["Marginal_extremes"]
    for var, group in extremes.groupby("variable"):
        group = group.dropna(subset=["return_level"])
        if len(group):
            ax2.plot(group.return_period_years, group.return_level, "o-", label=var)
    ax2.set(xscale="log", xlabel="Marginal return period (years)", ylabel="Source-specific units",
            title="Screening marginal extremes")
    from matplotlib.ticker import NullFormatter
    periods = sorted(set(result.project.return_periods))
    ax2.set_xticks(periods)
    ax2.set_xticklabels([f"{t:g}" for t in periods])
    ax2.xaxis.set_minor_formatter(NullFormatter())
    if ax2.lines:
        ax2.legend()
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", dpi=120)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def export_result(result, parent):
    parent = Path(parent)
    parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = parent / f"run_{timestamp}"
    target.mkdir()
    config = result.project.to_dict()
    # Export the metadata actually used, including a loaded/generated dataset.
    from dataclasses import asdict
    config["source_meta"] = asdict(result.primary.meta)
    # Reproducibility manifests retain basename IDs, not private absolute local paths.
    for key in ("primary_files", "comparison_files", "observation_files"):
        config[key] = [Path(p).name for p in config[key]]
    config_path = target / "analysis_settings.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    index_tables = {"Wind_Hs_occurrence", "Direction_occurrence"}
    with pd.ExcelWriter(target / "environmental_conditions.xlsx", engine="openpyxl") as writer:
        for name, frame in result.frames.items():
            clean = table_for_export(frame)
            clean.to_csv(target / f"{name}.csv", index=name in index_tables, encoding="utf-8-sig")
            clean.to_excel(writer, sheet_name=name[:31], index=name in index_tables)
            sheet = writer.sheets[name[:31]]
            sheet.freeze_panes = "B2" if name in index_tables else "A2"
            sheet.auto_filter.ref = sheet.dimensions
            for column in sheet.columns:
                width = min(62, max(12, max(len(str(c.value or "")) for c in list(column)[:150])+2))
                sheet.column_dimensions[column[0].column_letter].width = width
            from openpyxl.styles import Font, PatternFill
            for cell in sheet[1]:
                cell.font = Font(color="FFFFFF", bold=True)
                cell.fill = PatternFill("solid", fgColor="163C4B")
    title = html.escape(result.project.name)
    sections = []
    for name, frame in result.frames.items():
        display = frame.head(500)
        sections.append(f"<section><h2>{html.escape(name)}</h2>{display.to_html(index=name in index_tables, escape=True, float_format=lambda x: f'{x:.5g}')}"
                        + ("<p>보고서는 500행까지만 표시합니다. 전체 행은 CSV/Excel을 확인하세요.</p>" if len(frame)>500 else "") + "</section>")
    notes = "".join(f"<li>{html.escape(note)}</li>" for note in result.notes)
    image = _figure_data(result)
    report = f"""<!doctype html><html lang="ko"><meta charset="utf-8"><title>{title} · Metocean</title>
<style>body{{font:14px/1.7 'Segoe UI','Malgun Gothic',sans-serif;color:#183743;background:#f2f6f7;margin:0;padding:36px}}
main{{max-width:1350px;margin:auto}}h1{{font-size:30px}}h2{{font-size:19px}}.banner{{background:#fff0ce;border-left:5px solid #d79b2b;padding:18px}}section{{background:white;padding:22px;margin:20px 0;overflow:auto;border-radius:10px}}table{{border-collapse:collapse;font-size:12px;white-space:nowrap}}th,td{{border-bottom:1px solid #dce5e8;padding:8px;text-align:left}}th{{background:#e5f0f1}}img{{max-width:100%}}code{{word-break:break-all}}</style>
<main><p>OFFSHORE METOCEAN / ANALYSIS RECORD · v{__version__}</p><h1>{title}</h1>
<div class="banner"><strong>환경조건 후보 · 사용자 검토 필요</strong><ul>{notes}</ul></div>
<p>입력 좌표: {result.project.latitude:.5f}°, {result.project.longitude:.5f}° / 풍속 목표: {result.project.hub_height_m:g} m, {result.project.wind_average_minutes:g} min / 생성 UTC: {timestamp}</p>
<section><h2>Analysis overview</h2><img alt="Raw wind-wave pairs and marginal extremes" src="data:image/png;base64,{image}"></section>
{''.join(sections)}<section><h2>Provenance</h2><pre>{html.escape(json.dumps(result.primary.provenance, ensure_ascii=False, indent=2))}</pre></section></main></html>"""
    (target / "report.html").write_text(report, encoding="utf-8")
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(target.iterdir()) if p.is_file()}
    manifest = {"app_version": __version__, "created_utc": timestamp, "synthetic": result.primary.meta.synthetic,
                "source_provenance": {role: data.provenance for role, data in result.sources.items()}, "artifacts_sha256": hashes,
                "notes": result.notes,
                "statistical_reproducibility": "Same data, config, package versions and seed; file creation timestamps vary"}
    import scipy
    import sys
    manifest["runtime"] = {"python": sys.version.split()[0], "numpy": np.__version__,
                           "pandas": pd.__version__, "scipy": scipy.__version__}
    (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return target
