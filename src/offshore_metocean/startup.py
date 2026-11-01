"""Visible startup failures and local, rotating, credential-redacted application logs."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import sys


class RedactingFormatter(logging.Formatter):
    def format(self, record):
        text = super().format(record)
        for key in ("CDSAPI_KEY", "KMA_API_KEY", "GITHUB_TOKEN", "GH_TOKEN"):
            secret = os.environ.get(key)
            if secret:
                text = text.replace(secret, "[redacted]")
        return re.sub(r"(?i)((?:authkey|access_token|api[_-]?key|token)\s*[=:]\s*)([^\s&]+)",
                      r"\1[redacted]", text)


def log_directory():
    override = os.environ.get("OFFSHORE_METOCEAN_LOG_DIR")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "OffshoreMetocean" / "logs"
    return Path(os.environ.get("XDG_STATE_HOME", str(Path.home()/".local"/"state"))) / "offshore-metocean" / "logs"


def configure_logging():
    directory = log_directory()
    directory.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("offshore_metocean")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)
    handler = RotatingFileHandler(directory/"app.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return directory/"app.log"


def notify_failure(message):
    if sys.stderr is not None:
        print(message, file=sys.stderr)
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        if QApplication.instance():
            QMessageBox.critical(None, "Offshore Metocean · 실행 오류", message)
            return
    except ImportError:
        pass
    if os.name == "nt":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, "Offshore Metocean", 0x10)


def _run_gui(**kwargs):
    from .gui import main
    return main(**kwargs)


def launch_gui(config_path=None, demo=False, auto_run=False):
    log_path = None
    old_hook = sys.excepthook
    try:
        log_path = configure_logging()
        logger = logging.getLogger("offshore_metocean")
        from . import __version__
        logger.info("GUI starting; version=%s; demo=%s; auto_run=%s", __version__, demo, auto_run)
        def hook(kind, value, traceback):
            logger.error("Unhandled GUI exception", exc_info=(kind, value, traceback))
            notify_failure(f"GUI 처리 중 오류가 발생했습니다.\n로그: {log_path}")
        sys.excepthook = hook
        result = _run_gui(config_path=config_path, demo=demo, auto_run=auto_run)
        logger.info("GUI stopped; exit_code=%s", result)
        return result
    except Exception:
        if log_path:
            logging.getLogger("offshore_metocean").exception("GUI startup failed")
        notify_failure("앱을 시작하지 못했습니다. setup.ps1로 설치 상태와 설정 파일을 확인하세요.\n"
                       + (f"로그: {log_path}" if log_path else "로그 폴더를 만들 수 없습니다. 폴더 권한을 확인하세요."))
        return 1
    finally:
        sys.excepthook = old_hook
