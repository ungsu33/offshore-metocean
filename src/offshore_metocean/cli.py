from __future__ import annotations

import argparse
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offshore Metocean · GUI and reproducible screening analysis")
    sub = parser.add_subparsers(dest="command")
    gui = sub.add_parser("gui", help="GUI 실행")
    startup = gui.add_mutually_exclusive_group()
    startup.add_argument("--config", help="시작 시 불러올 JSON 설정")
    startup.add_argument("--demo", action="store_true", help="합성 예제 불러오기")
    gui.add_argument("--run", action="store_true", help="창을 연 뒤 분석 실행")
    demo = sub.add_parser("demo", help="합성 예제로 전체 분석·출력 확인 (실제 설계 사용 불가)")
    demo.add_argument("--output", default="results")
    run = sub.add_parser("run", help="저장된 JSON 설정으로 실행")
    run.add_argument("config")
    run.add_argument("--output", default="results")
    sub.add_parser("template", help="기본 JSON 설정 템플릿 출력")
    args = parser.parse_args(argv)
    if args.command in (None, "gui"):
        from .startup import launch_gui
        return launch_gui(config_path=getattr(args, "config", None), demo=getattr(args, "demo", False),
                          auto_run=getattr(args, "run", False))
    from .models import Project
    import json
    if args.command == "template":
        print(json.dumps(Project().to_dict(), ensure_ascii=False, indent=2))
        return 0
    from .analysis import run_analysis
    from .data import demo_datasets, load_files
    from .export import export_result
    if args.command == "demo":
        cfg = Project()
        cfg.demo_mode = True
        primary, comparison = demo_datasets()
        cfg.source_meta, cfg.comparison_meta = primary.meta, comparison.meta
        observation = None
    else:
        cfg = Project.load(Path(args.config))
        if cfg.demo_mode:
            from dataclasses import replace
            primary, comparison = demo_datasets()
            primary.meta = replace(cfg.source_meta, synthetic=True)
            comparison.meta = replace(cfg.comparison_meta, synthetic=True)
            observation = None
        else:
            primary = load_files(cfg.primary_files, cfg.source_meta, cfg)
            comparison = load_files(cfg.comparison_files, cfg.comparison_meta, cfg) if cfg.comparison_files else None
            observation = load_files(cfg.observation_files, cfg.observation_meta, cfg) if cfg.observation_files else None
    result = run_analysis(primary, cfg, comparison, observation,
                          progress=lambda value, message: print(f"[{value:3d}%] {message}"))
    print(export_result(result, args.output))
    return 0


def gui_entry():
    import sys
    return main(["gui", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
