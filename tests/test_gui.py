import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette

from offshore_metocean.gui import MainWindow, configure_app


def test_gui_analysis_export_and_stale_result_invalidation(qtbot, tmp_path, qapp):
    configure_app(qapp)
    assert qapp.palette().color(QPalette.ColorRole.Window).lightness() > 200
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.load_demo()
    window.bootstrap.setValue(20)
    qtbot.mouseClick(window.run_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: window.result is not None and not window.worker.isRunning(), timeout=60000)
    assert window.pages.currentIndex() == 4
    assert window.export_button.isEnabled()
    assert window.tables["DLC_candidates"].model().rowCount() > 0
    assert "합성" in window.result_notice.text()
    target = window.export_to(tmp_path)
    assert (target/"report.html").exists()
    screenshot_dir = os.environ.get("OFFSHORE_SCREENSHOT_DIR")
    if screenshot_dir:
        folder = Path(screenshot_dir)
        folder.mkdir(parents=True, exist_ok=True)
        window.canvas.draw()
        window.grab().save(str(folder/"gui-results.png"))
        window.nav.setCurrentRow(0)
        window.grab().save(str(folder/"gui-project.png"))
    window.latitude.setValue(36)
    assert window.result is None
    assert not window.export_button.isEnabled()
    assert not window.review_button.isEnabled()
    window.close()
