"""Native Qt GUI with background analysis, explicit metadata and reviewable exports."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, QThread, Signal, QUrl, QTimer
from PySide6.QtGui import QColor, QDesktopServices, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QListWidget, QMainWindow, QMessageBox, QProgressBar, QPushButton, QScrollArea, QSpinBox,
    QStackedWidget, QTabWidget, QTableView, QTableWidget, QTableWidgetItem, QTextBrowser,
    QVBoxLayout, QWidget)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from . import __version__
from .analysis import apply_review, run_analysis
from .data import Dataset, demo_datasets, load_files
from .export import export_result
from .models import DLCRule, Project, SourceMeta, STATUS_LABELS
from .providers import CATALOG, download_era5, era5_metadata, era5_requests, save_request_plan
from .tasks import TaskCancelled

STYLE = """
QMainWindow,QWidget#workspace{background:#f1f5f7;color:#183644}
QWidget{font-size:12px;color:#183644}
QFrame#sidebar{background:#102f3b;border:0} QFrame#sidebar QLabel{color:#c5d9df}
QLabel#brand{color:white;font-size:23px;font-weight:700} QLabel#eyebrow{color:#168a80;font-size:10px;font-weight:700}
QListWidget#navigation{background:transparent;border:0;color:#bed0d6;outline:0;font-size:13px}
QListWidget#navigation::item{padding:15px 12px;margin:3px 0;border-radius:7px}
QListWidget#navigation::item:selected{background:#24505b;color:white;border-left:3px solid #5cd0bc}
QLabel#heading{font-size:26px;font-weight:700;color:#163643} QLabel#subtitle{color:#6b808a;font-size:12px}
QFrame#topbar{background:white;border-bottom:1px solid #dce5e8}
QGroupBox{background:white;border:1px solid #dce5e8;border-radius:9px;margin-top:16px;padding:20px 16px 15px;font-weight:600}
QGroupBox::title{subcontrol-origin:margin;left:16px;padding:0 5px;color:#315563}
QLineEdit,QDoubleSpinBox,QSpinBox,QComboBox{background:#f8fafb;border:1px solid #cfdbdf;border-radius:5px;padding:6px;min-height:21px;selection-background-color:#4db9aa}
QLineEdit:focus,QDoubleSpinBox:focus,QSpinBox:focus,QComboBox:focus{border:1px solid #168a80}
QComboBox::drop-down{border:0;width:22px}
QPushButton{background:white;border:1px solid #cbdadd;border-radius:6px;padding:9px 14px;font-weight:600}
QPushButton:hover{background:#e6f3f0;border-color:#59a99d}
QPushButton#primary{background:#157f75;color:white;border:0;min-width:110px}
QPushButton:disabled{background:#e3e9eb;color:#8ca0a8;border-color:#dce5e8}
QLabel#notice{background:#fff3d7;border:1px solid #f0dcab;border-radius:7px;padding:12px;color:#7a5c20}
QTableView,QTableWidget{border:1px solid #d9e3e6;background:white;alternate-background-color:#f5f9fa;gridline-color:#e7eef0;selection-background-color:#d4ece7;selection-color:#163c4b}
QHeaderView::section{background:#e9f1f3;color:#325561;border:0;border-bottom:1px solid #d9e3e6;padding:9px;font-weight:600}
QTabWidget::pane{background:white;border:1px solid #dce5e8;border-radius:6px}
QTabBar::tab{padding:10px 13px;background:#e9eff1;color:#54707b}
QTabBar::tab:selected{background:white;color:#117e72;border-bottom:3px solid #178f80}
QTextBrowser{background:white;border:1px solid #dce5e8;padding:12px}
QProgressBar{border:0;background:#dce8e9;border-radius:4px;min-height:9px;max-height:9px}
QProgressBar::chunk{background:#19a18e;border-radius:4px} QScrollArea{border:0;background:transparent}
QFrame#metric{background:white;border:1px solid #dce5e8;border-radius:8px}
QLabel#metricvalue{font-size:25px;font-weight:700;color:#123f49}
"""


def button(text, callback, primary=False):
    b = QPushButton(text)
    if primary:
        b.setObjectName("primary")
    b.clicked.connect(callback)
    return b


def num(value=0, low=0, high=100000, decimals=2):
    w = QDoubleSpinBox()
    w.setRange(low, high)
    w.setDecimals(decimals)
    w.setValue(value)
    w.setKeyboardTracking(False)
    return w


def integer(value, low, high):
    w = QSpinBox()
    w.setRange(low, high)
    w.setValue(value)
    w.setKeyboardTracking(False)
    return w


def notice(text):
    return QLabel(text, objectName="notice", wordWrap=True)


def page():
    content = QWidget()
    box = QVBoxLayout(content)
    box.setContentsMargins(2, 2, 10, 14)
    box.setSpacing(15)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(content)
    return scroll, box


class FrameModel(QAbstractTableModel):
    def __init__(self, frame, parent=None):
        super().__init__(parent)
        self.frame = frame.copy()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.frame)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.frame.columns)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        value = self.frame.iloc[index.row(), index.column()]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            if value is None or pd.isna(value):
                return "—"
            if isinstance(value, (float, np.floating)):
                return f"{value:.5g}"
            return STATUS_LABELS.get(str(value), str(value))
        if role == Qt.ItemDataRole.BackgroundRole:
            return {"withheld": QColor("#fff0df"), "provisional": QColor("#fff8d9"),
                    "eligible": QColor("#e1f4e9"), "demo": QColor("#e4f0fa")}.get(str(value))
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole:
            return str(self.frame.columns[section]) if orientation == Qt.Orientation.Horizontal else str(section+1)
        return None


class Worker(QThread):
    progress = Signal(int, str)
    success = Signal(object)
    failure = Signal(str)
    cancelled = Signal()

    def __init__(self, task, parent=None):
        super().__init__(parent)
        self.task = task
        from threading import Event
        self.cancel_requested = Event()

    def cancel(self):
        self.cancel_requested.set()

    def checkpoint(self):
        if self.cancel_requested.is_set():
            raise TaskCancelled()

    def report_progress(self, value, message):
        self.checkpoint()
        self.progress.emit(value, message)

    def run(self):
        try:
            self.checkpoint()
            result = self.task(self.report_progress, self.checkpoint)
            self.checkpoint()
            self.success.emit(result)
        except TaskCancelled:
            self.cancelled.emit()
        except Exception as exc:
            import os
            message = str(exc)
            for name in ("CDSAPI_KEY", "KMA_API_KEY", "GITHUB_TOKEN", "GH_TOKEN"):
                if os.environ.get(name):
                    message = message.replace(os.environ[name], "[redacted]")
            self.failure.emit(f"{type(exc).__name__}: {message}")


class MetadataEditor(QWidget):
    changed = Signal()

    def __init__(self, meta):
        super().__init__()
        self.base = deepcopy(meta)
        self.controls = {}
        layout = QGridLayout(self)
        definitions = [
            ("name", "자료 이름", "text"), ("provider", "제공기관", "text"),
            ("version", "모델/제품 버전", "text"), ("kind", "자료 유형", ["hindcast", "reanalysis", "observation"]),
            ("reference", "변수 정의·출처 문서", "text"), ("lineage", "모델·강제력 생성 계보", "text"),
            ("atmosphere_grid_km", "원래 대기 격자 (km)", "float"), ("wave_grid_km", "원래 파랑 격자 (km)", "float"),
            ("forcing_grid_km", "바람 강제력 격자 (km)", "float"), ("delivery_grid_km", "제공/보간 격자 (km)", "float"),
            ("integration_minutes", "모델 적분 간격 (min)", "float"), ("output_minutes", "자료 제공 간격 (min)", "float"),
            ("wind_statistic", "풍속 시간 정의", ["unknown", "mean", "instantaneous", "maximum"]),
            ("wind_average_minutes", "풍속 평균시간 (min)", "float"), ("wind_height_m", "풍속 기준 높이 (m)", "float"),
            ("period_definition", "원자료 파주기", ["unknown", "Tp", "Tm01", "Tm02", "Tz"]),
            ("direction_convention", "방향 (진북 기준)", ["unknown", "from", "to"]), ("timezone", "시간대", "text"),
            ("site_assessment", "지점 대표성 검토 근거", "text")]
        for i, (key, label, kind) in enumerate(definitions):
            if kind == "text":
                control = QLineEdit()
                control.textChanged.connect(self.changed)
            elif kind == "float":
                control = num()
                control.setSpecialValueText("미확인")
                control.valueChanged.connect(self.changed)
            else:
                control = QComboBox()
                control.addItems(kind)
                control.currentTextChanged.connect(self.changed)
            self.controls[key] = control
            layout.addWidget(QLabel(label), i//2, (i % 2)*2)
            layout.addWidget(control, i//2, (i % 2)*2+1)
        self.homogeneous = QCheckBox("분석기간의 모델·강제력 변경 이력을 확인함")
        self.homogeneous.toggled.connect(self.changed)
        layout.addWidget(self.homogeneous, 10, 0, 1, 4)
        layout.addWidget(QLabel("0 = 미확인 · 단위: m/s, m, s, deg · 출력 간격을 평균시간으로 대체하지 않습니다."), 11, 0, 1, 4)
        self.set_meta(meta)

    def set_meta(self, meta):
        self.base = deepcopy(meta)
        for key, control in self.controls.items():
            value = getattr(meta, key)
            if isinstance(control, QLineEdit):
                control.setText(str(value or ""))
            elif isinstance(control, QComboBox):
                control.setCurrentText(str(value))
            else:
                control.setValue(value or 0)
        self.homogeneous.setChecked(meta.homogeneous)

    def get_meta(self):
        meta = deepcopy(self.base)
        for key, control in self.controls.items():
            value = control.text().strip() if isinstance(control, QLineEdit) else (
                control.currentText() if isinstance(control, QComboBox) else control.value() or None)
            setattr(meta, key, value)
        meta.homogeneous = self.homogeneous.isChecked()
        return meta


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Offshore Metocean · Wind & Wave Design Workbench")
        self.resize(1460, 940)
        self.setMinimumSize(1120, 760)
        if QApplication.platformName() != "offscreen" and QApplication.primaryScreen():
            available = QApplication.primaryScreen().availableGeometry()
            self.resize(min(1460, max(1120, available.width()-48)),
                        min(940, max(760, available.height()-60)))
        self.cfg, self.result, self.demo, self.worker = Project(), None, None, None
        self._loading, self._revision = True, 0
        self._close_after_task = False
        self.files = {"primary": [], "comparison": [], "observation": []}
        root = QWidget(objectName="workspace")
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        sidebar = QFrame(objectName="sidebar")
        sidebar.setFixedWidth(220)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(20, 30, 18, 24)
        side.addWidget(QLabel("OCEAN ENGINEERING", objectName="eyebrow"))
        side.addWidget(QLabel("Offshore\nMetocean", objectName="brand"))
        side.addSpacing(30)
        self.nav = QListWidget(objectName="navigation")
        self.nav.addItems(["01   프로젝트", "02   자료 & 메타데이터", "03   분석 옵션", "04   DLC 규칙", "05   결과 & 검토", "06   데이터 소스"])
        side.addWidget(self.nav, 1)
        side.addWidget(QLabel("WIND + WAVE\n한국 해역 · 환경조건 후보\nIEC / DNV 기반 검토"))
        side.addSpacing(20)
        side.addWidget(QLabel(f"DESKTOP WORKBENCH / v{__version__}"))
        layout.addWidget(sidebar)
        right = QVBoxLayout()
        right.setSpacing(0)
        top = QFrame(objectName="topbar")
        bar = QHBoxLayout(top)
        bar.setContentsMargins(26, 14, 26, 14)
        bar.addWidget(QLabel("WORKSPACE / METOCEAN DESIGN BASIS", objectName="eyebrow"))
        bar.addStretch()
        bar.addWidget(button("설정 열기", self.open_project))
        bar.addWidget(button("설정 저장", self.save_project))
        bar.addWidget(button("합성 예제", self.load_demo))
        self.run_button = button("분석 실행 →", self.start_analysis, True)
        bar.addWidget(self.run_button)
        self.cancel_button = button("작업 취소", self.request_cancel)
        self.cancel_button.setEnabled(False)
        bar.addWidget(self.cancel_button)
        right.addWidget(top)
        body = QVBoxLayout()
        body.setContentsMargins(26, 23, 26, 18)
        self.title = QLabel(objectName="heading")
        self.subtitle = QLabel(objectName="subtitle", wordWrap=True)
        body.addWidget(self.title)
        body.addWidget(self.subtitle)
        body.addSpacing(10)
        self.pages = QStackedWidget()
        for builder in (self.project_page, self.data_page, self.options_page, self.rules_page,
                        self.results_page, self.catalog_page):
            self.pages.addWidget(builder())
        body.addWidget(self.pages, 1)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setValue(0)
        body.addWidget(self.progress)
        self.status_label = QLabel("프로젝트 설정 또는 합성 예제로 시작하세요. 실제 설계자료는 직접 불러옵니다.", objectName="subtitle", wordWrap=True)
        body.addWidget(self.status_label)
        right.addLayout(body, 1)
        layout.addLayout(right, 1)
        self.nav.currentRowChanged.connect(self.select_page)
        self.nav.setCurrentRow(0)
        self._loading = False

    def track(self, control):
        if isinstance(control, QLineEdit):
            control.textChanged.connect(self.invalidate)
        elif isinstance(control, QComboBox):
            control.currentTextChanged.connect(self.invalidate)
        elif isinstance(control, QCheckBox):
            control.toggled.connect(self.invalidate)
        else:
            control.valueChanged.connect(self.invalidate)
        return control

    def invalidate(self, *_):
        if self._loading:
            return
        self._revision += 1
        self.result = None
        self.export_button.setEnabled(False)
        self.review_button.setEnabled(False)
        self.status_label.setText("입력이 변경되었습니다. 결과를 갱신하려면 다시 분석하세요.")
        self.result_notice.setText("설정 변경 / 이전 표시 결과는 유효하지 않습니다. 다시 분석하세요.")

    def select_page(self, index):
        titles = [("지점에서 시작하는 설계환경조건", "위치·설계 정의 → 자료 적합성 → 통계·극한 후보 → DLC 검토"),
                  ("자료의 정의부터 확인합니다", "주자료·비교 hindcast·관측을 구분하고 원래 격자와 시간 정의를 기록합니다."),
                  ("분석 방법과 채택 기준", "초기 수치는 screening 값입니다. 프로젝트 적용 근거를 검토해 조정하세요."),
                  ("DLC 환경조건 매핑", "실제 DLC 번호·판본·원문 조항은 사용자가 확인합니다."),
                  ("결과와 설계 검토 기록", "자료 적합성, 극치 후보, DLC 검토 상태와 근거를 함께 확인합니다."),
                  ("공개 데이터 소스", "ERA5 다운로드 또는 국내외 확보 자료를 가져옵니다. 제품별 가용성·정의를 확인하세요.")]
        self.pages.setCurrentIndex(index)
        self.title.setText(titles[index][0])
        self.subtitle.setText(titles[index][1])

    def project_page(self):
        widget, layout = page()
        layout.addWidget(notice("첫 버전: 바람·파랑 / 운영단계 / 개념·기본설계. 조류·수위·구조응답은 별도 검토 대상입니다."))
        grid = QGridLayout()
        group = QGroupBox("PROJECT / SITE")
        f = QFormLayout(group)
        self.project_name = self.track(QLineEdit(self.cfg.name))
        self.latitude = self.track(num(35.5, -90, 90, 5))
        self.longitude = self.track(num(125.8, -180, 180, 5))
        self.hub_height = self.track(num(150, .1, 500, 1))
        for label, control in [("프로젝트 이름", self.project_name), ("위도 (° N / WGS84)", self.latitude),
                               ("경도 (° E / WGS84)", self.longitude), ("목표 풍속 높이 (m)", self.hub_height)]:
            f.addRow(label, control)
        grid.addWidget(group, 0, 0)
        group = QGroupBox("DESIGN DEFINITIONS")
        f = QFormLayout(group)
        self.target_average = self.track(num(10, .1, 180, 1))
        self.target_period = self.track(QComboBox())
        self.target_period.addItems(["Tp", "Tm01", "Tm02", "Tz"])
        self.basis = self.track(QLineEdit(self.cfg.standard_basis))
        f.addRow("설계 평균시간 (min)", self.target_average)
        f.addRow("목표 파주기 정의", self.target_period)
        f.addRow("기준·판본 검토 기록", self.basis)
        f.addRow(QLabel("좌표·높이·평균시간은 입력 시작값입니다.\n실제 프로젝트의 정의로 바꾸세요."))
        grid.addWidget(group, 0, 1)
        layout.addLayout(grid)
        group = QGroupBox("WORKFLOW / EVIDENCE")
        box = QVBoxLayout(group)
        for title, description in [
            ("01  자료를 선택하고 생성 계보를 기록", "대기·파랑·강제력 격자와 배포 격자, 모델 버전·장기 동질성을 구분합니다."),
            ("02  시간 정의와 비교 근거를 점검", "제공 간격 ≠ 평균시간. 관측 또는 복수 hindcast 비교 근거가 없으면 보류합니다."),
            ("03  후보를 계산하고 검토", "조건부 분포와 개별 극값 시나리오를 제공하며, 공동 재현기간·인증 적합성을 자동 확정하지 않습니다.")]:
            box.addWidget(QLabel(title, objectName="eyebrow"))
            box.addWidget(QLabel(description, wordWrap=True))
            box.addSpacing(12)
        layout.addWidget(group)
        layout.addStretch()
        return widget

    def data_page(self):
        widget, layout = page()
        layout.addWidget(button("CSV 템플릿 저장", self.save_csv_template))
        tabs = QTabWidget()
        self.meta_editors, self.file_labels = {}, {}
        for role, title, meta in [("primary", "주자료", self.cfg.source_meta),
                ("comparison", "비교 hindcast", self.cfg.comparison_meta), ("observation", "관측자료", self.cfg.observation_meta)]:
            panel = QWidget()
            box = QVBoxLayout(panel)
            tools = QHBoxLayout()
            tools.addWidget(button("파일 선택", lambda checked=False, r=role: self.choose_files(r)))
            tools.addWidget(button("선택 해제", lambda checked=False, r=role: self.clear_files(r)))
            tools.addWidget(button("메타데이터 JSON", lambda checked=False, r=role: self.open_metadata(r)))
            tools.addStretch()
            box.addLayout(tools)
            self.file_labels[role] = QLabel("선택된 파일 없음", objectName="subtitle", wordWrap=True)
            box.addWidget(self.file_labels[role])
            editor = MetadataEditor(meta)
            editor.changed.connect(self.invalidate)
            self.meta_editors[role] = editor
            box.addWidget(editor)
            tabs.addTab(panel, title)
        layout.addWidget(tabs)
        group = QGroupBox("CSV 열 매핑 · 비워두면 표준 별칭 사용")
        grid = QGridLayout(group)
        self.column_edits = {}
        for i, key in enumerate(("timestamp", "wind_speed", "hs", "tp", "wind_dir", "wave_dir")):
            self.column_edits[key] = self.track(QLineEdit())
            self.column_edits[key].setPlaceholderText(key)
            grid.addWidget(QLabel(key), i//3*2, i % 3)
            grid.addWidget(self.column_edits[key], i//3*2+1, i % 3)
        layout.addWidget(group)
        layout.addWidget(notice("높이·평균시간·주기 정의가 정합돼야 비교 근거로 사용됩니다. 여러 월별/변수별 파일을 선택하면 결합하며, 값 충돌은 오류로 처리합니다."))
        layout.addStretch()
        return widget

    def options_page(self):
        widget, layout = page()
        layout.addWidget(notice("유효 연수·자료 충족률·오차 한계·신뢰수준은 초기 설정이며 IEC/DNV의 확정 요구 수치가 아닙니다."))
        grid = QGridLayout()
        self.method = self.track(QComboBox())
        self.method.addItems(["Gumbel", "GEV"])
        self.return_periods = self.track(QLineEdit("2, 10, 50"))
        self.min_years, self.coverage = self.track(integer(10, 3, 100)), self.track(num(.8, .1, 1))
        self.bootstrap, self.confidence = self.track(integer(100, 20, 2000)), self.track(num(.95, .5, .999, 3))
        self.min_pairs, self.nrmse = self.track(integer(30, 3, 100000)), self.track(num(.3, .001, 10, 3))
        self.max_grid, self.max_output = self.track(num(0, 0, 1000)), self.track(num(0, 0, 10000))
        self.max_grid.setSpecialValueText("상한 미지정")
        self.max_output.setSpecialValueText("상한 미지정")
        self.provisional = self.track(QCheckBox("비교 근거에 따른 조건부 잠정값 허용"))
        self.provisional.setChecked(True)
        self.criteria = self.track(QCheckBox("수치 기준을 프로젝트에 대해 검토·확정함"))
        self.wind_width, self.cut_in = self.track(num(2, .1, 10)), self.track(num(3, 0, 100))
        self.cut_out, self.sectors = self.track(num(25, .1, 100)), self.track(integer(12, 1, 72))
        self.shear_enabled = self.track(QCheckBox("근거를 명시한 power-law 높이 보정"))
        self.shear, self.shear_ref = self.track(num(.14, 0, .6, 3)), self.track(QLineEdit())
        groups = [
            ("EXTREMES / UNCERTAINTY", [("연최대 분포", self.method), ("재현기간 (년, >1)", self.return_periods),
             ("최소 충족 연수", self.min_years), ("연간 자료 충족률", self.coverage), ("Bootstrap 반복", self.bootstrap), ("신뢰수준", self.confidence)]),
            ("VALIDATION / SUITABILITY", [("최소 비교 표본", self.min_pairs), ("NRMSE 상한", self.nrmse),
             ("원래/강제력 격자 상한 km", self.max_grid), ("제공 간격 상한 min", self.max_output), (None, self.provisional), (None, self.criteria)]),
            ("CONDITIONAL / DIRECTIONAL", [("풍속 구간 폭 m/s", self.wind_width), ("운전 하한 m/s", self.cut_in),
             ("운전 상한 m/s", self.cut_out), ("방향 분할 수", self.sectors)]),
            ("OPTIONAL / WIND HEIGHT", [(None, self.shear_enabled), ("전단 지수 α", self.shear), ("적용 근거·유효범위", self.shear_ref)])]
        for i, (title, rows) in enumerate(groups):
            group = QGroupBox(title)
            f = QFormLayout(group)
            for label, control in rows:
                f.addRow(label, control) if label else f.addRow(control)
            if i == 3:
                f.addRow(QLabel("평균시간 변환은 지원하지 않습니다.\n순간 풍속을 10분 평균으로 간주하지 않습니다."))
            grid.addWidget(group, i//2, i % 2)
        layout.addLayout(grid)
        layout.addStretch()
        return widget

    def rules_page(self):
        widget, layout = page()
        layout.addWidget(notice("OP-*는 내부 템플릿이며 IEC DLC 번호가 아닙니다. 원문 기준·판본·조항 확인 후만 체크하세요. 난류·돌풍·OEM 입력은 별도입니다."))
        tools = QHBoxLayout()
        tools.addWidget(button("행 추가", lambda: self.add_rule()))
        tools.addWidget(button("선택 행 삭제", self.remove_rules))
        tools.addStretch()
        layout.addLayout(tools)
        self.rule_table = QTableWidget(0, 5)
        self.rule_table.setHorizontalHeaderLabels(["DLC / 내부 ID", "운영상황", "입력 기반", "기준명 · 판본 · 조항", "원문 확인"])
        self.rule_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.rule_table.setMinimumHeight(300)
        for rule in self.cfg.rules:
            self.append_rule(rule)
        self.rule_table.itemChanged.connect(self.invalidate)
        layout.addWidget(self.rule_table)
        layout.addWidget(QLabel("normal: 경험적 조건부 분포 / extreme: 개별 극값·경험 동반조건 / external: 외부 입력 필요", wordWrap=True))
        layout.addStretch()
        return widget

    def append_rule(self, rule):
        row = self.rule_table.rowCount()
        self.rule_table.insertRow(row)
        for col, text in ((0, rule.identifier), (1, rule.situation), (3, rule.code_reference)):
            self.rule_table.setItem(row, col, QTableWidgetItem(text))
        combo = QComboBox()
        combo.addItems(["normal", "extreme", "external"])
        combo.setCurrentText(rule.basis)
        combo.currentTextChanged.connect(self.invalidate)
        self.rule_table.setCellWidget(row, 2, combo)
        item = QTableWidgetItem()
        item.setCheckState(Qt.CheckState.Checked if rule.verified else Qt.CheckState.Unchecked)
        self.rule_table.setItem(row, 4, item)

    def add_rule(self):
        self.append_rule(DLCRule(f"USER-{self.rule_table.rowCount()+1}", "사용자 정의"))
        self.invalidate()

    def remove_rules(self):
        for row in sorted({x.row() for x in self.rule_table.selectedIndexes()}, reverse=True):
            self.rule_table.removeRow(row)
        self.invalidate()

    def results_page(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        self.result_notice = notice("분석 결과가 없습니다. 자료 또는 합성 예제를 불러온 뒤 분석하세요.")
        layout.addWidget(self.result_notice)
        row = QHBoxLayout()
        self.metrics = []
        for title in ("RECORDS", "DATA SPAN", "ENV. CANDIDATES", "WITHHELD / DEMO"):
            card = QFrame(objectName="metric")
            box = QVBoxLayout(card)
            box.addWidget(QLabel(title, objectName="eyebrow"))
            label = QLabel("—", objectName="metricvalue")
            box.addWidget(label)
            self.metrics.append(label)
            row.addWidget(card)
        layout.addLayout(row)
        self.result_tabs = QTabWidget()
        self.figure = Figure(figsize=(10, 5), layout="constrained", facecolor="white")
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.result_tabs.addTab(self.canvas, "차트")
        self.tables = {}
        for key, title in [("Data_quality", "자료 적합성"), ("Validation", "검증"), ("Conditional", "조건부 통계"),
                           ("Marginal_extremes", "극치"), ("DLC_candidates", "DLC 후보"),
                           ("Wind_Hs_T_occurrence", "결합빈도"), ("Annual_maxima", "연최대")]:
            table = QTableView()
            table.setAlternatingRowColors(True)
            table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
            self.tables[key] = table
            self.result_tabs.addTab(table, title)
        self.notes_view = QTextBrowser()
        self.result_tabs.addTab(self.notes_view, "이력·한계")
        layout.addWidget(self.result_tabs, 1)
        tools = QHBoxLayout()
        self.review_button = button("선택 후보 · 예비 채택", lambda: self.review("accepted_preliminary"))
        self.review_button.setEnabled(False)
        tools.addWidget(self.review_button)
        tools.addWidget(button("선택 후보 · 반려", lambda: self.review("rejected")))
        tools.addStretch()
        self.export_button = button("Excel · CSV · 보고서 내보내기", self.export_dialog, True)
        self.export_button.setEnabled(False)
        tools.addWidget(self.export_button)
        layout.addLayout(tools)
        return widget

    def catalog_page(self):
        widget, layout = page()
        for entry in CATALOG:
            group = QGroupBox(entry["name"])
            box = QVBoxLayout(group)
            for key in ("type", "native_grid", "time", "access"):
                box.addWidget(QLabel(entry[key], wordWrap=True))
            box.addWidget(button("공식 자료 안내", lambda checked=False, u=entry["url"]: QDesktopServices.openUrl(QUrl(u))))
            layout.addWidget(group)
        group = QGroupBox("ERA5 / CDS 요청")
        form = QFormLayout(group)
        self.first_year = self.track(integer(2000, 1940, 2100))
        self.last_year = self.track(integer(2020, 1940, 2100))
        form.addRow("시작 연도", self.first_year)
        form.addRow("끝 연도", self.last_year)
        row = QHBoxLayout()
        row.addWidget(button("API 요청 계획 저장", self.save_cds_plan))
        self.cds_button = button("CDS 다운로드 실행", self.start_cds)
        row.addWidget(self.cds_button)
        form.addRow(row)
        form.addRow(QLabel("개인 CDS 계정·토큰 설정과 데이터셋 약관 동의 필요. 토큰은 앱에 저장하지 않습니다.\n대기·파랑을 월별 분리 요청하며 순간 풍속은 10분 평균으로 자동 변환하지 않습니다.", wordWrap=True))
        layout.addWidget(group)
        layout.addStretch()
        return widget

    def collect(self):
        cfg = deepcopy(self.cfg)
        for key, control in self.field_map().items():
            value = control.text() if isinstance(control, QLineEdit) else (control.currentText()
                if isinstance(control, QComboBox) else control.isChecked() if isinstance(control, QCheckBox) else control.value())
            setattr(cfg, key, value)
        try:
            cfg.return_periods = [float(t.strip()) for t in self.return_periods.text().split(",")]
        except ValueError as exc:
            raise ValueError("재현기간은 쉼표로 나눈 숫자로 입력하세요.") from exc
        cfg.max_grid_km = self.max_grid.value() or None
        cfg.max_output_minutes = self.max_output.value() or None
        cfg.shear_exponent = self.shear.value() if self.shear_enabled.isChecked() else None
        cfg.shear_reference = self.shear_ref.text().strip()
        for role, key in [("primary", "source_meta"), ("comparison", "comparison_meta"), ("observation", "observation_meta")]:
            setattr(cfg, key, self.meta_editors[role].get_meta())
            setattr(cfg, role+"_files", list(self.files[role]))
        cfg.column_map = {k: e.text().strip() for k, e in self.column_edits.items() if e.text().strip()}
        cfg.rules = [DLCRule(self.rule_table.item(r, 0).text().strip(), self.rule_table.item(r, 1).text().strip(),
            self.rule_table.cellWidget(r, 2).currentText(), self.rule_table.item(r, 3).text().strip(),
            self.rule_table.item(r, 4).checkState() == Qt.CheckState.Checked) for r in range(self.rule_table.rowCount())]
        cfg.demo_mode = self.demo is not None
        cfg.validate()
        return cfg

    def field_map(self):
        return {"name": self.project_name, "latitude": self.latitude, "longitude": self.longitude,
            "hub_height_m": self.hub_height, "wind_average_minutes": self.target_average,
            "period_definition": self.target_period, "standard_basis": self.basis, "extreme_method": self.method,
            "min_complete_years": self.min_years, "min_annual_coverage": self.coverage,
            "bootstrap_samples": self.bootstrap, "confidence": self.confidence,
            "min_validation_pairs": self.min_pairs, "max_nrmse": self.nrmse,
            "allow_provisional": self.provisional, "criteria_confirmed": self.criteria,
            "wind_bin_width": self.wind_width, "cut_in": self.cut_in, "cut_out": self.cut_out,
            "direction_sectors": self.sectors}

    def load_settings(self, cfg):
        self._loading = True
        self.cfg = cfg
        for key, control in self.field_map().items():
            value = getattr(cfg, key)
            if isinstance(control, QLineEdit):
                control.setText(value)
            elif isinstance(control, QComboBox):
                control.setCurrentText(value)
            elif isinstance(control, QCheckBox):
                control.setChecked(value)
            else:
                control.setValue(value)
        self.return_periods.setText(", ".join(str(t) for t in cfg.return_periods))
        self.max_grid.setValue(cfg.max_grid_km or 0)
        self.max_output.setValue(cfg.max_output_minutes or 0)
        self.shear_enabled.setChecked(cfg.shear_exponent is not None)
        self.shear.setValue(cfg.shear_exponent if cfg.shear_exponent is not None else .14)
        self.shear_ref.setText(cfg.shear_reference)
        for role, key in [("primary", "source_meta"), ("comparison", "comparison_meta"), ("observation", "observation_meta")]:
            self.meta_editors[role].set_meta(getattr(cfg, key))
            self.files[role] = list(getattr(cfg, role+"_files"))
            self.file_labels[role].setText(" / ".join(Path(p).name for p in self.files[role]) or "선택된 파일 없음")
        for key, edit in self.column_edits.items():
            edit.setText(cfg.column_map.get(key, ""))
        self.rule_table.setRowCount(0)
        for rule in cfg.rules:
            self.append_rule(rule)
        self._loading = False
        self.invalidate()

    def open_project(self):
        path, _ = QFileDialog.getOpenFileName(self, "프로젝트 설정", "", "Project (*.json)")
        if path:
            try:
                cfg = Project.load(path)
                self.demo = demo_datasets() if cfg.demo_mode else None
                self.load_settings(cfg)
            except Exception as exc:
                self.show_error(str(exc))

    def save_project(self):
        try:
            cfg = self.collect()
            path, _ = QFileDialog.getSaveFileName(self, "설정 저장", "project.local.json", "Project (*.json)")
            if path:
                cfg.save(path)
                self.status_label.setText(f"설정 저장: {Path(path).name}")
        except Exception as exc:
            self.show_error(str(exc))

    def load_demo(self):
        self.demo = demo_datasets()
        self._loading = True
        for role, dataset in zip(("primary", "comparison"), self.demo):
            self.meta_editors[role].set_meta(dataset.meta)
            self.file_labels[role].setText(dataset.meta.name + " / 2000–2019 / synthetic, not site data")
        self.files = {"primary": [], "comparison": [], "observation": []}
        self.file_labels["observation"].setText("관측 없음")
        self._loading = False
        self.invalidate()
        self.status_label.setText("합성 예제를 불러왔습니다. 실제 지점 자료가 아니며 설계 채택은 차단됩니다. 분석을 실행하세요.")
        self.nav.setCurrentRow(1)

    def choose_files(self, role):
        paths, _ = QFileDialog.getOpenFileNames(self, "자료 선택", "", "Metocean (*.csv *.nc *.nc4 *.netcdf);;All (*)")
        if paths:
            if self.demo is not None:
                self.demo = None
                for r in self.files:
                    self.files[r] = []
                    self.file_labels[r].setText("선택된 파일 없음")
                    self.meta_editors[r].set_meta(SourceMeta(name=r, kind="observation" if r == "observation" else "hindcast"))
            self.files[role] = paths
            self.file_labels[role].setText(" / ".join(Path(p).name for p in paths))
            self.file_labels[role].setToolTip("\n".join(paths))
            self.invalidate()

    def clear_files(self, role):
        if self.demo:
            self.demo = None
            for r in self.files:
                self.files[r] = []
                self.file_labels[r].setText("선택된 파일 없음")
        else:
            self.files[role] = []
            self.file_labels[role].setText("선택된 파일 없음")
        self.invalidate()

    def open_metadata(self, role):
        path, _ = QFileDialog.getOpenFileName(self, "자료 메타데이터", "", "Metadata (*.json)")
        if path:
            try:
                self.meta_editors[role].set_meta(SourceMeta(**json.loads(Path(path).read_text(encoding="utf-8-sig"))))
                self.invalidate()
            except Exception as exc:
                self.show_error(str(exc))

    def save_csv_template(self):
        path, _ = QFileDialog.getSaveFileName(self, "CSV 템플릿", "metocean_template.csv", "CSV (*.csv)")
        if path:
            Path(path).write_text("timestamp,wind_speed,hs,tp,wind_dir,wave_dir\n", encoding="utf-8-sig")

    def start_analysis(self):
        if self.worker and self.worker.isRunning():
            return
        try:
            cfg = self.collect()
            if self.demo is None and not cfg.primary_files:
                raise ValueError("주자료 CSV/NetCDF를 선택하거나 합성 예제를 불러오세요.")
            demo, revision = self.demo, self._revision
            self.result = None
            self.export_button.setEnabled(False)
            self.review_button.setEnabled(False)
            def task(progress, checkpoint):
                checkpoint()
                if demo:
                    primary = Dataset(demo[0].frame, replace(cfg.source_meta, synthetic=True), demo[0].provenance)
                    comparison = Dataset(demo[1].frame, replace(cfg.comparison_meta, synthetic=True), demo[1].provenance)
                    observation = None
                else:
                    primary = load_files(cfg.primary_files, cfg.source_meta, cfg)
                    comparison = load_files(cfg.comparison_files, cfg.comparison_meta, cfg) if cfg.comparison_files else None
                    observation = load_files(cfg.observation_files, cfg.observation_meta, cfg) if cfg.observation_files else None
                return revision, run_analysis(primary, cfg, comparison, observation, progress, checkpoint)
            self.launch_worker(task, self.analysis_done)
        except Exception as exc:
            self.show_error(str(exc))

    def launch_worker(self, task, handler):
        self.worker = Worker(task)
        self.worker.progress.connect(self.on_progress)
        self.worker.success.connect(handler)
        self.worker.failure.connect(self.show_error)
        self.worker.cancelled.connect(self.on_cancelled)
        self.worker.finished.connect(self.finish_task)
        self.run_button.setEnabled(False)
        self.cds_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress.setValue(0)
        self.worker.start()

    def request_cancel(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.cancel_button.setEnabled(False)
            self.status_label.setText("취소 요청을 받았습니다. 다음 안전 지점에서 중단합니다. 진행 중인 CDS 요청은 응답 후 중단됩니다.")

    def on_cancelled(self):
        self.progress.setValue(0)
        self.status_label.setText("작업을 취소했습니다. 설정과 완료된 다운로드 파일은 유지됩니다.")
        if self.result is None:
            self.result_notice.setText("작업 취소 / 완료된 분석 결과가 없습니다. 설정을 확인한 뒤 다시 실행할 수 있습니다.")

    def finish_task(self):
        self.run_button.setEnabled(True)
        self.cds_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        if self._close_after_task:
            self.close()

    def on_progress(self, value, message):
        self.progress.setValue(value)
        self.status_label.setText(message)

    def analysis_done(self, payload):
        if self.worker and self.worker.cancel_requested.is_set():
            self.on_cancelled()
            return
        revision, result = payload
        if revision != self._revision:
            self.status_label.setText("분석 중 설정 변경: 결과를 보류했습니다. 현재 설정으로 다시 분석하세요.")
            return
        self.result = result
        self.refresh_results()
        self.export_button.setEnabled(True)
        self.review_button.setEnabled(True)
        self.nav.setCurrentRow(4)
        self.status_label.setText("분석 완료 · 자료 적합성·극치·DLC 후보를 검토하고 결과물을 내보낼 수 있습니다.")

    def refresh_results(self):
        result = self.result
        raw, quality, dlc = result.primary.frame, result.frames["Data_quality"], result.frames["DLC_candidates"]
        values = [f"{len(raw):,}", f"{raw.index[0].year}–{raw.index[-1].year}", str(len(dlc)),
                  f"{sum(quality.status == 'withheld')} / {sum(quality.status == 'demo')}"]
        for widget, value in zip(self.metrics, values):
            widget.setText(value)
        self.result_notice.setText("합성 예제 / 실제 해역 자료가 아니며 설계 채택 불가" if result.primary.meta.synthetic else
            "환경조건 후보 / 자료 적합성·DLC 원문 매핑·최종 조합 검토 필요. 잠정값은 개념·기본설계용입니다.")
        for key, table in self.tables.items():
            table.setModel(FrameModel(result.frames[key], table))
            table.resizeColumnsToContents()
            for col in range(table.model().columnCount()):
                table.setColumnWidth(col, min(360, table.columnWidth(col)))
        import html
        self.notes_view.setHtml("<h3>분석 이력과 한계</h3><ul>"+"".join(f"<li>{html.escape(n)}</li>" for n in result.notes)+
            "</ul><h3>자료 추적</h3><pre>"+html.escape(json.dumps(result.primary.provenance, ensure_ascii=False, indent=2))+"</pre>")
        self.draw_charts()

    def draw_charts(self):
        self.figure.clear()
        a, b, c, d = self.figure.subplots(2, 2).ravel()
        frame = self.result.primary.frame
        sample = frame.iloc[::max(1, len(frame)//3000)]
        a.plot(sample.index, sample.wind_speed, lw=.55, color="#268b91")
        a.set(title="SOURCE WIND / time history", ylabel="m/s")
        b.scatter(sample.wind_speed, sample.hs, s=5, alpha=.22, c="#138f8c", edgecolors="none")
        b.set(title="WIND–WAVE / source pairs", xlabel="Wind speed (m/s)", ylabel="Hs (m)")
        conditional = self.result.frames["Conditional"]
        if len(conditional):
            c.plot(conditional.wind_mean, conditional.hs_p50, "o-", ms=3, color="#128a7d", label="Hs p50")
            c.plot(conditional.wind_mean, conditional.hs_p90, "--", color="#d39a43", label="Hs p90")
            c.legend(fontsize=8, frameon=False)
        c.set(title="CONDITIONAL SEA STATE", xlabel="Target mean wind (m/s)", ylabel="Hs (m)")
        for var, group in self.result.frames["Marginal_extremes"].groupby("variable"):
            group = group.dropna(subset=["return_level"])
            if len(group):
                d.plot(group.return_period_years, group.return_level, "o-", ms=3, label=var)
        d.set(title="MARGINAL EXTREMES / not joint T-year", xlabel="Return period (years)", xscale="log")
        if d.lines:
            d.legend(fontsize=8, frameon=False)
        from matplotlib.ticker import NullFormatter
        d.set_xticks(sorted(set(self.result.project.return_periods)))
        d.set_xticklabels([f"{t:g}" for t in sorted(set(self.result.project.return_periods))])
        d.xaxis.set_minor_formatter(NullFormatter())
        for ax in (a, b, c, d):
            ax.tick_params(labelsize=8)
            ax.title.set_fontsize(10)
            ax.spines[["top", "right"]].set_visible(False)
        self.canvas.draw_idle()

    def review(self, decision):
        if self.result is None:
            return
        table = self.tables["DLC_candidates"]
        indices = sorted({i.row() for i in table.selectionModel().selectedRows()})
        if not indices:
            self.show_error("DLC 후보 탭에서 행을 선택하세요.")
            return
        try:
            apply_review(self.result, indices, decision)
            self.refresh_results()
            self.status_label.setText(f"{len(indices)}개 후보 검토 기록 완료")
        except ValueError as exc:
            self.show_error(str(exc))

    def export_to(self, directory):
        if self.result is None:
            raise ValueError("유효한 결과가 없습니다. 현재 설정으로 다시 분석하세요.")
        return export_result(self.result, directory)

    def export_dialog(self):
        if self.result is None:
            return
        folder = QFileDialog.getExistingDirectory(self, "결과물 폴더")
        if folder:
            try:
                destination = self.export_to(folder)
                self.status_label.setText(f"내보내기 완료: {destination}")
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(destination)))
            except Exception as exc:
                self.show_error(str(exc))

    def cds_plan(self):
        return era5_requests(self.latitude.value(), self.longitude.value(), self.first_year.value(), self.last_year.value())

    def save_cds_plan(self):
        try:
            requests = self.cds_plan()
            folder = QFileDialog.getExistingDirectory(self, "요청 계획 폴더")
            if folder:
                self.status_label.setText(str(save_request_plan(requests, folder)))
        except Exception as exc:
            self.show_error(str(exc))

    def start_cds(self):
        if self.worker and self.worker.isRunning():
            return
        try:
            requests = self.cds_plan()
            folder = QFileDialog.getExistingDirectory(self, "ERA5 다운로드 폴더")
            if folder:
                revision = self._revision
                self.launch_worker(lambda progress, checkpoint: (
                    revision, download_era5(requests, folder, progress, checkpoint)), self.cds_done)
        except Exception as exc:
            self.show_error(str(exc))

    def cds_done(self, payload):
        revision, files = payload
        if self.worker and self.worker.cancel_requested.is_set():
            self.on_cancelled()
            return
        if revision != self._revision:
            self.status_label.setText("다운로드 파일은 저장됐지만 입력 조건이 변경돼 자동 연결은 보류했습니다. 올바른 지점·기간의 파일을 직접 선택하세요.")
            return
        self.demo = None
        self.files = {"primary": files, "comparison": [], "observation": []}
        self.meta_editors["primary"].set_meta(era5_metadata())
        for role in self.files:
            self.file_labels[role].setText(f"ERA5 파일 {len(files)}개" if role == "primary" else "선택된 파일 없음")
        self.invalidate()
        self.nav.setCurrentRow(1)
        self.status_label.setText("다운로드 완료. 제공 격자·공간 대표성을 확인하고 비교자료를 추가하세요. 순간 풍속의 평균시간은 변환하지 않습니다.")

    def show_error(self, text):
        import logging
        logging.getLogger("offshore_metocean").warning("Task/input error: %s", text)
        self.status_label.setText(text)
        QMessageBox.warning(self, "입력·자료 확인", text)

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self._close_after_task = True
            self.request_cancel()
            self.status_label.setText("종료 요청: 작업을 안전하게 중단한 뒤 창을 닫습니다. 현재 CDS 요청은 응답을 기다릴 수 있습니다.")
            event.ignore()
        else:
            event.accept()


def configure_app(app):
    import os
    # Windows offscreen Qt has no system font database; register installed fonts explicitly.
    font_folder = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for filename in ("malgun.ttf", "malgunbd.ttf", "segoeui.ttf", "segoeuib.ttf"):
        path = font_folder / filename
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))
    available = QFontDatabase.families()
    family = next((name for name in ("Malgun Gothic", "Noto Sans CJK KR", "Segoe UI", "DejaVu Sans")
                   if name in available), "sans-serif")
    app.setStyle("Fusion")
    palette = QPalette()
    for role, color in ((QPalette.ColorRole.Window, "#f1f5f7"),
                        (QPalette.ColorRole.WindowText, "#183644"),
                        (QPalette.ColorRole.Base, "#ffffff"),
                        (QPalette.ColorRole.AlternateBase, "#f5f9fa"),
                        (QPalette.ColorRole.Text, "#183644"),
                        (QPalette.ColorRole.Button, "#ffffff"),
                        (QPalette.ColorRole.ButtonText, "#183644"),
                        (QPalette.ColorRole.Highlight, "#d4ece7"),
                        (QPalette.ColorRole.HighlightedText, "#163c4b")):
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    app.setFont(QFont(family, 10))
    app.setStyleSheet(STYLE)


def main(config_path=None, demo=False, auto_run=False):
    app = QApplication.instance() or QApplication(sys.argv[:1])
    configure_app(app)
    window = MainWindow()
    if config_path:
        cfg = Project.load(config_path)
        window.demo = demo_datasets() if cfg.demo_mode else None
        window.load_settings(cfg)
    elif demo:
        window.load_demo()
    window.show()
    if auto_run:
        QTimer.singleShot(0, window.start_analysis)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
