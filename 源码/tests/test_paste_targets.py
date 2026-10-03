"""从管理窗口呼出时不能误粘贴到先前使用过的软件。"""
from PySide6.QtWidgets import QApplication, QWidget
from clipnest.core.repository import Repository
from clipnest.ui.jobs import Jobs
from clipnest.ui.windows import QuickWindow
from clipnest.platform import windows as win_api


def test_manager_focus_clears_stale_target_but_quick_search_keeps_source(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    jobs = Jobs()
    repo = Repository(tmp_path / "history.db", tmp_path / "images")
    quick = QuickWindow(repo, jobs, lambda *args: None, paste=lambda handle: (True, "fake"))
    manager = QWidget()
    current = [int(manager.winId())]
    monkeypatch.setattr(win_api, "foreground_window", lambda: current[0])
    try:
        quick._target_window = 12345
        quick.remember_target()
        assert quick._target_window == 0
        quick._target_window = 54321
        current[0] = int(quick.winId())
        quick.remember_target()
        assert quick._target_window == 54321
    finally:
        quick.hide()
        manager.deleteLater()
        quick.deleteLater()
        jobs.close()
        app.processEvents()
