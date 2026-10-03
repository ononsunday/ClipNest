"""使用真实 Qt 控件验证异步搜索、预览和键盘流程，无生产历史。"""
import time
from pathlib import Path
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog
from clipnest.core.repository import Repository
from clipnest.platform.settings import Settings
from clipnest.platform.clipboard import ClipboardMonitor
from clipnest.platform.windows import HotkeyManager
from clipnest.ui.jobs import Jobs
from clipnest.ui.windows import MainWindow, SearchPane
from clipnest.ui.dialogs import TextToolsDialog
from clipnest.ui.theme import apply_theme


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def until(app, condition, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return
        time.sleep(0.01)
    assert condition(), "Qt condition timed out"


@pytest.fixture
def ui(qt_app, tmp_path, monkeypatch):
    # UI 回归绝不把用户剪贴板粘贴到真实软件。
    from clipnest.platform import windows as win_api
    monkeypatch.setattr(win_api, "paste_to_window", lambda handle: (True, "已发送粘贴快捷键"))
    repo = Repository(tmp_path / "history.db", tmp_path / "images")
    settings = Settings(tmp_path)
    settings.set("paused", True)
    jobs = Jobs()
    monitor = ClipboardMonitor(settings)
    hotkey = HotkeyManager()
    apply_theme(qt_app, "light")
    window = MainWindow(repo, settings, jobs, monitor, hotkey, qt_app)
    window.show()
    yield qt_app, repo, window, jobs
    window.exiting = True
    window.quick.hide()
    window.close()
    monitor.stop()
    hotkey.unregister()
    jobs.close()
    qt_app.processEvents()


def test_stage1_history_search_favorite_pin(ui):
    app, repo, window, jobs = ui
    first = repo.add_text("课程资料：剪贴板")
    repo.add_text("ordinary text")
    repo.update(first["id"], favorite=True, pinned=True)
    window.refresh()
    until(app, lambda: window.pane.list.count() == 2 and window.item is not None)
    assert window.pane.list.item(0).data(Qt.ItemDataRole.UserRole)["id"] == first["id"]
    window.pane.search.setText("课程")
    until(app, lambda: window.pane.list.count() == 1 and not window.pane.busy)
    assert window.item["text"] == "课程资料：剪贴板"
    assert window.fav_btn.text() == "取消收藏"
    assert window.pin_btn.text() == "取消置顶"


def test_quick_keyboard_navigation_and_escape(ui):
    app, repo, window, jobs = ui
    repo.add_text("first")
    repo.add_text("second")
    window.quick.summon()
    until(app, lambda: window.quick.pane.list.count() == 2)
    QTest.mouseClick(window.quick.pane.search, Qt.MouseButton.LeftButton)
    assert window.quick.pane.search.hasFocus()
    QTest.keyClick(window.quick.pane.search, Qt.Key.Key_Down)
    assert window.quick.pane.list.currentRow() == 1
    selected = []
    window.quick.pane.copy_requested.disconnect()
    window.quick.pane.copy_requested.connect(selected.append)
    QTest.keyClick(window.quick.pane.search, Qt.Key.Key_Return)
    assert selected[0]["text"] == "first"
    QTest.keyClick(window.quick, Qt.Key.Key_Escape)
    until(app, lambda: not window.quick.isVisible())


def test_text_tools_preview_never_overwrites(ui):
    app, repo, window, jobs = ui
    item = repo.add_text('{"a":1}')
    copied, saved = [], []
    dialog = TextToolsDialog(item["text"], jobs, copied.append, saved.append, window)
    dialog.operation.setCurrentIndex(dialog.operation.findData("json"))
    dialog.preview()
    until(app, lambda: dialog.copy_btn.isEnabled())
    assert '\n' in dialog.output.toPlainText()
    dialog.copy_btn.click()
    dialog.save_btn.click()
    assert copied == saved
    assert repo.get(item["id"])["text"] == '{"a":1}'
    dialog.input.setPlainText("invalid")
    assert not dialog.copy_btn.isEnabled()
    dialog.preview()
    until(app, lambda: "无法处理" in dialog.status.text())
    assert not dialog.save_btn.isEnabled()
    dialog.reject()


def test_code_preview_preserves_original_indentation(ui):
    app, repo, window, jobs = ui
    source = "def hi():\n    return 1\n"
    item = repo.add_text(source)
    window.refresh()
    until(app, lambda: window.item and window.item["id"] == item["id"] and "return 1" in window.text.toPlainText())
    assert repo.get(item["id"])["text"] == source


def test_close_to_tray_keeps_window_hidden(ui):
    class VisibleTray:
        def isVisible(self):
            return True
    app, repo, window, jobs = ui
    window.tray = VisibleTray()
    window.close()
    assert not window.isVisible()
    assert not window.exiting


def test_late_search_cannot_replace_new_query(ui):
    app, repo, window, jobs = ui
    repo.add_text("old query")
    repo.add_text("new query")
    original = repo.search
    def delayed(**params):
        if params.get("query") == "old":
            time.sleep(0.25)
        return original(**params)
    repo.search = delayed
    pane = window.pane
    pane.search.setText("old")
    pane.refresh()
    pane.search.setText("new")
    pane.refresh()
    until(app, lambda: pane.list.count() == 1 and pane.list.item(0).data(Qt.ItemDataRole.UserRole)["text"] == "new query")
    QTest.qWait(350)
    assert pane.list.item(0).data(Qt.ItemDataRole.UserRole)["text"] == "new query"


def test_image_preview(ui):
    from PySide6.QtGui import QImage
    app, repo, window, jobs = ui
    image = QImage(80, 60, QImage.Format.Format_ARGB32)
    image.fill(0xFF55AAEE)
    picture = repo.add_image(image)
    window.refresh()
    until(app, lambda: window.item and window.item["id"] == picture["id"] and not window.image.pixmap().isNull())
    assert window.stack.currentIndex() == 1
    assert not window.edit_content_btn.isEnabled()
    assert not window.tools_btn.isEnabled()


def test_copy_failure_does_not_show_success(ui):
    app, repo, window, jobs = ui
    item = repo.add_text("synthetic copy failure")
    window.monitor.copy_text = lambda text: False
    window.copy_item(item)
    until(app, lambda: "复制失败" in window.statusBar().currentMessage())
    assert "已复制" not in window.statusBar().currentMessage()


def test_close_without_tray_requests_quit(ui):
    app, repo, window, jobs = ui
    class QuitRecorder:
        called = False
        def quit(self):
            self.called = True
    recorder = QuitRecorder()
    window.app = recorder
    window.close()
    assert recorder.called


def test_metadata_language_contract(ui):
    from clipnest.ui.dialogs import MetadataDialog
    app, repo, window, jobs = ui
    item = repo.add_text("#include <iostream>\nint main(){std::cout << 1;}\n")
    dialog = MetadataDialog(item, ["常用代码"], window)
    dialog.language.setCurrentText("C++")
    dialog.tags.setText("C++, 学习")
    fields = dialog.fields()
    updated = repo.update(item["id"], **fields)
    assert updated["language"] == "C++"
    assert updated["tags"] == ["C++", "学习"]
    assert updated["text"] == item["text"]
