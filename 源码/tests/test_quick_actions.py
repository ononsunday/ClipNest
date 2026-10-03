"""粘贴和整理动作隔离回归；全部使用临时库和假剪贴板/粘贴回调。"""
import time

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from clipnest.core.repository import Repository
from clipnest.platform.settings import Settings
from clipnest.platform.windows import HotkeyManager
from clipnest.platform import windows as win_api
from clipnest.ui.jobs import Jobs
from clipnest.ui.paste import PasteController
from clipnest.ui.windows import MainWindow, QuickWindow


def until(app, condition, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return
        time.sleep(0.01)
    assert condition(), "Qt condition timed out"


class FakeMonitor:
    paused = True

    def __init__(self, copied):
        self.copied = copied

    def copy_text(self, text):
        self.copied.append(text)
        return True

    def copy_image(self, image):
        self.copied.append(image)
        return True

    def set_paused(self, value):
        self.paused = value


@pytest.fixture(params=["quick", "main"])
def action_ui(request, tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    repo = Repository(tmp_path / "history.db", tmp_path / "images")
    settings = Settings(tmp_path)
    jobs = Jobs()
    copied, pasted = [], []
    state = {"foreground": 4242, "result": (True, "已发送粘贴快捷键")}
    monkeypatch.setattr(win_api, "foreground_window", lambda: state["foreground"])
    monkeypatch.setattr(win_api, "window_is_valid", lambda handle: handle == 4242)
    monkeypatch.setattr(win_api, "modifiers_down", lambda: False)
    monkeypatch.setattr(win_api, "paste_gesture_down", lambda: False)
    monkeypatch.setattr(win_api, "restore_foreground", lambda handle: True)

    def send(handle):
        pasted.append(handle)
        return state["result"]

    monkeypatch.setattr(win_api, "paste_to_window", send)
    if request.param == "quick":
        def copy(record, done=None):
            copied.append(record["text"])
            if done:
                done()
        window = QuickWindow(repo, jobs, copy, settings=settings, paste=send)
        window.summon()
    else:
        window = MainWindow(repo, settings, jobs, FakeMonitor(copied), HotkeyManager(), app)
        window.show()
    yield request.param, app, repo, settings, window, copied, pasted, state
    if getattr(window.pane, "context", None):
        window.pane.context.hide()
    if request.param == "main":
        window.exiting = True
        window.quick.hide()
    window.hide()
    jobs.close()
    window.deleteLater()
    app.processEvents()


def add_and_refresh(ui, protected=False):
    kind, app, repo, settings, window, copied, pasted, state = ui
    ordinary = repo.add_text("ordinary history")
    records = [ordinary]
    if protected:
        favorite = repo.add_text("favorite history")
        pinned = repo.add_text("pinned history")
        records += [repo.update(favorite["id"], favorite=True), repo.update(pinned["id"], pinned=True)]
    window.pane.refresh()
    until(app, lambda: window.pane.list.count() == len(records) and not window.pane.busy)
    return records


def body_point(pane, index=0):
    rectangle = pane.list.visualItemRect(pane.list.item(index))
    return QPoint(rectangle.left() + 90, rectangle.center().y())


@pytest.mark.parametrize("close", [False, True])
@pytest.mark.parametrize("action_ui", ["quick"], indirect=True)
def test_single_click_pastes_once_and_respects_close_setting(action_ui, close):
    kind, app, repo, settings, window, copied, pasted, state = action_ui
    add_and_refresh(action_ui)
    settings.set("close_after_paste", close)
    QTest.mouseClick(window.pane.list.viewport(), Qt.MouseButton.LeftButton, pos=body_point(window.pane))
    until(app, lambda: len(pasted) == 1)
    assert copied == ["ordinary history"] and pasted == [4242]
    assert window.isVisible() is (not close)
    assert window.passive


@pytest.mark.parametrize("action_ui", ["quick"], indirect=True)
def test_failed_paste_keeps_window_open_even_when_close_enabled(action_ui):
    kind, app, repo, settings, window, copied, pasted, state = action_ui
    add_and_refresh(action_ui)
    settings.set("close_after_paste", True)
    state["result"] = (False, "测试目标拒绝粘贴，可手动 Ctrl+V")
    QTest.mouseClick(window.pane.list.viewport(), Qt.MouseButton.LeftButton, pos=body_point(window.pane))
    until(app, lambda: len(pasted) == 1)
    assert copied == ["ordinary history"]
    assert window.isVisible() and "Ctrl+V" in window.hint.text()


@pytest.mark.parametrize("action_ui", ["quick"], indirect=True)
def test_async_copy_focus_change_cancels_paste(action_ui):
    kind, app, repo, settings, window, copied, pasted, state = action_ui
    record = add_and_refresh(action_ui)[0]
    settings.set("close_after_paste", True)
    pending = []
    window.choose(record, lambda item, done: pending.append(done))
    assert len(pending) == 1 and pasted == []
    state["foreground"] = 4343
    pending[0]()
    app.processEvents()
    assert pasted == [] and window.isVisible()
    assert "焦点已切换" in window.hint.text() and "Ctrl+V" in window.hint.text()


def test_delete_hit_area_deletes_without_copy_or_paste(action_ui):
    kind, app, repo, settings, window, copied, pasted, state = action_ui
    record = add_and_refresh(action_ui)[0]
    rectangle = window.pane.list.delete_button_rect(window.pane.list.item(0))
    QTest.mouseClick(window.pane.list.viewport(), Qt.MouseButton.LeftButton, pos=rectangle.center())
    until(app, lambda: repo.get(record["id"]) is None and window.pane.list.count() == 0)
    assert copied == pasted == []


@pytest.mark.parametrize("field,label", [("favorite", "收藏"), ("pinned", "置顶")])
def test_context_metadata_action_never_pastes(action_ui, field, label):
    kind, app, repo, settings, window, copied, pasted, state = action_ui
    record = add_and_refresh(action_ui)[0]
    point = body_point(window.pane)
    QTest.mouseClick(window.pane.list.viewport(), Qt.MouseButton.RightButton, pos=point)
    assert copied == pasted == []
    window.pane.context_menu(point)
    menu = window.pane.context
    action = next(action for action in menu.actions() if action.text() == label)
    action.trigger()
    menu.hide()
    until(app, lambda: repo.get(record["id"])[field])
    assert copied == pasted == []


@pytest.mark.parametrize("operation", ["selected", "all"])
@pytest.mark.parametrize("include_protected", [False, True])
def test_bulk_and_all_delete_protect_favorite_and_pin_by_default(action_ui, monkeypatch, operation, include_protected):
    kind, app, repo, settings, window, copied, pasted, state = action_ui
    records = add_and_refresh(action_ui, protected=True)
    confirmations = []

    def approve(dialog):
        checkbox = dialog.checkBox()
        assert checkbox is not None and not checkbox.isChecked()
        assert dialog.defaultButton() == dialog.button(QMessageBox.StandardButton.No)
        confirmations.append(dialog.windowTitle())
        checkbox.setChecked(include_protected)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "exec", approve)
    if operation == "selected":
        window.pane.bulk_button.click()
        assert window.pane.list.is_bulk_mode()
        for index in range(window.pane.list.count()):
            QTest.mouseClick(window.pane.list.viewport(), Qt.MouseButton.LeftButton,
                pos=body_point(window.pane, index))
        assert len(window.pane.list.selected_records()) == 3
        assert copied == pasted == []
        window.pane.delete_selected_button.click()
    else:
        window.pane.search.setText("ordinary")
        until(app, lambda: window.pane.list.count() == 1 and not window.pane.busy and not window.pane.timer.isActive())
        window.pane.clear_button.click()
    expected_count = 0 if include_protected else 2
    until(app, lambda: repo.stats()["total"] == expected_count)
    assert confirmations and copied == pasted == []
    assert repo.get(records[0]["id"]) is None
    if not include_protected:
        assert repo.get(records[1]["id"])["favorite"]
        assert repo.get(records[2]["id"])["pinned"]


def test_cancel_bulk_delete_preserves_all_selected_records(action_ui, monkeypatch):
    kind, app, repo, settings, window, copied, pasted, state = action_ui
    records = add_and_refresh(action_ui, protected=True)
    monkeypatch.setattr(QMessageBox, "exec", lambda dialog: QMessageBox.StandardButton.No)
    window.pane.bulk_button.click()
    window.pane.select_all_button.click()
    assert len(window.pane.list.selected_records()) == 3
    window.pane.delete_selected_button.click()
    app.processEvents()
    assert repo.stats()["total"] == 3
    assert copied == pasted == []


def test_paste_controller_waits_for_held_enter_before_restoring_and_sending(monkeypatch):
    app = QApplication.instance() or QApplication([])
    state = {"foreground": 101, "held": True}
    restored, sent, completed = [], [], []
    monkeypatch.setattr(win_api, "foreground_window", lambda: state["foreground"])
    monkeypatch.setattr(win_api, "window_is_valid", lambda handle: handle == 4242)
    monkeypatch.setattr(win_api, "paste_gesture_down", lambda: state["held"])

    def restore(handle):
        restored.append(handle)
        state["foreground"] = handle
        return True

    def send(handle):
        sent.append(handle)
        return True, "已发送粘贴快捷键"

    monkeypatch.setattr(win_api, "restore_foreground", restore)
    controller = PasteController(None, send)
    try:
        controller.start(4242, {101}, lambda ok, message: completed.append(ok))
        QTest.qWait(70)
        assert restored == sent == completed == []
        state["held"] = False
        until(app, lambda: bool(completed))
        assert restored == sent == [4242] and completed == [True]
    finally:
        controller.cancel()
