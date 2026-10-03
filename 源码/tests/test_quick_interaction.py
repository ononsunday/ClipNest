"""快捷窗的用户交互回归；不监听或写入真实剪贴板。"""
import time

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from clipnest.core.repository import Repository
from clipnest.platform.settings import Settings
from clipnest.platform import windows as win_api
from clipnest.ui.jobs import Jobs
from clipnest.ui.windows import QuickWindow


def until(app, condition, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return
        time.sleep(0.01)
    assert condition(), "Qt condition timed out"


@pytest.fixture
def quick_ui(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    repo = Repository(tmp_path / "history.db", tmp_path / "images")
    settings = Settings(tmp_path)
    jobs = Jobs()
    copied = []
    monkeypatch.setattr(win_api, "paste_gesture_down", lambda: False)

    def copy(item, done=None):
        copied.append(item)
        if done:
            done()

    # 该组只测焦点/复制界面，不向用户原窗口发送 Ctrl+V。
    window = QuickWindow(repo, jobs, copy, settings=settings,
        paste=lambda handle: (True, "已发送粘贴快捷键"))
    yield app, repo, settings, window, copied
    window.hide()
    jobs.close()
    window.deleteLater()
    app.processEvents()


def drag(widget, delta):
    """发送带 LeftButton 状态的 Qt 事件，避免移动用户的真实鼠标。"""
    local = QPoint(20, max(5, widget.height() // 2))
    start_global = widget.mapToGlobal(local)
    QTest.mousePress(widget, Qt.MouseButton.LeftButton, pos=local)
    move = QMouseEvent(
        QEvent.Type.MouseMove, QPointF(local + delta), QPointF(start_global + delta),
        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(widget, move)
    release = QMouseEvent(
        QEvent.Type.MouseButtonRelease, QPointF(local), QPointF(start_global + delta),
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(widget, release)


def test_open_and_return_to_original_app_keeps_quick_visible(quick_ui):
    app, repo, settings, quick, copied = quick_ui
    quick.summon()
    until(app, quick.isVisible)
    assert quick.passive
    assert not quick.pane.search.hasFocus()
    # 回到原软件会产生失焦通知，窗口应继续可见。
    QApplication.sendEvent(quick, QEvent(QEvent.Type.WindowDeactivate))
    app.processEvents()
    assert quick.isVisible()


def test_click_search_then_keyboard_search_and_copy(quick_ui):
    app, repo, settings, quick, copied = quick_ui
    repo.add_text("学习资料")
    repo.add_text("other text")
    quick.summon()
    until(app, lambda: quick.pane.list.count() == 2)
    QTest.mouseClick(quick.pane.search, Qt.MouseButton.LeftButton)
    until(app, lambda: quick.pane.search.hasFocus())
    assert not quick.passive
    quick.pane.search.setText("学习")
    until(app, lambda: quick.pane.list.count() == 1 and not quick.pane.busy)
    QTest.keyClick(quick.pane.search, Qt.Key.Key_Return)
    assert copied[0]["text"] == "学习资料"
    assert quick.isVisible()
    quick.activate_search()
    until(app, lambda: quick.pane.search.hasFocus())
    QTest.keyClick(quick, Qt.Key.Key_Escape)
    until(app, lambda: not quick.isVisible())


def test_double_click_search_activates_after_copy_returns_to_passive(quick_ui):
    app, repo, settings, quick, copied = quick_ui
    repo.add_text("copy then search again")
    quick.summon()
    until(app, lambda: quick.pane.list.count() == 1)
    QTest.mouseClick(quick.pane.search, Qt.MouseButton.LeftButton)
    until(app, lambda: quick.pane.search.hasFocus())
    QTest.keyClick(quick.pane.search, Qt.Key.Key_Return)
    assert quick.passive and quick.isVisible()
    # Windows 会把同处的快速再次点击识别为双击；也应进入搜索。
    QTest.mouseDClick(quick.pane.search, Qt.MouseButton.LeftButton)
    until(app, lambda: quick.pane.search.hasFocus() and not quick.passive)


def test_single_click_paste_stays_visible_and_passive(quick_ui):
    app, repo, settings, quick, copied = quick_ui
    repo.add_text("test-owned history")
    quick.summon()
    until(app, lambda: quick.pane.list.count() == 1)
    row = quick.pane.list.item(0)
    center = quick.pane.list.visualItemRect(row).center()
    QTest.mouseClick(quick.pane.list.viewport(), Qt.MouseButton.LeftButton, pos=center)
    until(app, lambda: len(copied) == 1)
    assert copied[0]["text"] == "test-owned history"
    assert quick.isVisible()
    assert quick.passive


def test_drag_title_remembers_position_on_reopen(quick_ui):
    app, repo, settings, quick, copied = quick_ui
    quick.summon()
    until(app, quick.isVisible)
    origin = quick.pos()
    delta = QPoint(37, 29)
    drag(quick.drag_header, delta)
    until(app, lambda: quick.pos() != origin)
    assert quick.pos() == origin + delta
    assert quick.passive
    remembered = [quick.x(), quick.y()]
    until(app, lambda: settings.get("quick_position") == remembered)
    saved = Settings(settings.data_dir)
    assert saved.get("quick_position") == remembered
    quick.hide()
    quick.summon()
    until(app, quick.isVisible)
    assert [quick.x(), quick.y()] == remembered


def test_position_from_missing_monitor_is_clamped_to_visible_screen(quick_ui):
    app, repo, settings, quick, copied = quick_ui
    settings.set("quick_position", [100000, -100000])
    quick.summon()
    until(app, quick.isVisible)
    frame = quick.frameGeometry()
    assert any(screen.availableGeometry().contains(frame) for screen in app.screens())
    assert quick.passive


def test_repeated_summon_returns_to_passive_without_repositioning(quick_ui):
    app, repo, settings, quick, copied = quick_ui
    quick.summon()
    until(app, quick.isVisible)
    drag(quick.drag_header, QPoint(19, 11))
    expected = quick.pos()
    quick.activate_search()
    until(app, lambda: quick.pane.search.hasFocus())
    quick.summon()
    app.processEvents()
    assert quick.isVisible()
    assert quick.passive
    assert not quick.pane.search.hasFocus()
    assert quick.pos() == expected
