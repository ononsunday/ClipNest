"""插画主题回归：临时数据、Qt 控件与模拟回调，不接触系统剪贴板。"""
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QLabel

from clipnest.core.repository import Repository
from clipnest.platform import windows as win_api
from clipnest.platform.settings import Settings
from clipnest.platform.windows import HotkeyManager
from clipnest.ui.art import ArtWidget, art_pixmap
from clipnest.ui.jobs import Jobs
from clipnest.ui.theme import apply_theme
from clipnest.ui import windows as ui_windows
from clipnest.ui.windows import MainWindow


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

    def copy_text(self, text):
        raise AssertionError("主题测试不应复制文本")

    def copy_image(self, image):
        raise AssertionError("主题测试不应复制图片")

    def set_paused(self, paused):
        self.paused = paused


@pytest.fixture
def themed_ui(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(win_api, "foreground_window", lambda: 0)
    monkeypatch.setattr(win_api, "restore_foreground", lambda handle: False)
    monkeypatch.setattr(win_api, "show_no_activate_topmost", lambda window: window.show())
    monkeypatch.setattr(win_api, "set_window_no_activate", lambda *args: None)

    def reject_paste(handle):
        raise AssertionError("主题测试不应发送粘贴快捷键")

    monkeypatch.setattr(win_api, "paste_to_window", reject_paste)
    repo = Repository(tmp_path / "history.db", tmp_path / "images")
    settings = Settings(tmp_path)
    settings.set("paused", True)
    jobs = Jobs()
    apply_theme(app, "light")
    window = MainWindow(repo, settings, jobs, FakeMonitor(), HotkeyManager(), app)
    window.show()
    yield app, repo, settings, jobs, window
    window.exiting = True
    window.quick.hide()
    window.close()
    jobs.close()
    window.deleteLater()
    app.processEvents()
    apply_theme(app, "light")


def test_packaged_art_loads_from_another_working_directory(themed_ui, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for filename in ("banner.png", "portrait.jpg", "sleep.jpg", "quick_chibi.png", "nav_stickers.png"):
        pixmap = art_pixmap(filename, max_width=320)
        assert not pixmap.isNull(), filename
        assert max(pixmap.width(), pixmap.height()) <= 320


def test_decorative_art_cannot_intercept_drag_or_search(themed_ui):
    app, repo, settings, jobs, window = themed_ui
    art = window.findChildren(ArtWidget) + window.quick.findChildren(ArtWidget)
    assert art
    for widget in art:
        assert widget.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        assert widget.focusPolicy() == Qt.FocusPolicy.NoFocus


def test_settings_theme_change_updates_portrait_without_recreating_window(themed_ui, monkeypatch):
    app, repo, settings, jobs, window = themed_ui
    theme = {"value": "dark"}

    class SavedSettingsDialog:
        def __init__(self, *_):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def values(self):
            keys = ("theme", "hotkey", "max_items", "max_age_days", "startup",
                    "close_after_paste", "excluded_apps")
            values = {key: settings.get(key) for key in keys}
            values["theme"] = theme["value"]
            return values

    monkeypatch.setattr(ui_windows, "SettingsDialog", SavedSettingsDialog)
    monkeypatch.setattr(window.hotkey, "register", lambda value: (True, ""))
    portrait = window.portrait
    assert not portrait.isVisible()
    window.open_settings()
    until(app, lambda: portrait.isVisible())
    assert app.property("clipnestDark") and settings.get("theme") == "dark"
    theme["value"] = "light"
    window.open_settings()
    until(app, lambda: not portrait.isVisible())
    assert window.portrait is portrait and not app.property("clipnestDark")


def test_reordered_code_and_tag_navigation_filters_real_records(themed_ui):
    app, repo, settings, jobs, window = themed_ui
    code = repo.add_text("def greeting():\n    return 'hello'\n")
    tagged = repo.add_text("课程记录")
    repo.update(tagged["id"], tags=["课程"])
    window.refresh()
    until(app, lambda: window.tags.findData("课程") >= 0)
    code_entry = window.nav.findItems("代码片段", Qt.MatchFlag.MatchExactly)[0]
    window.nav.setCurrentItem(code_entry)
    until(app, lambda: not window.pane.busy and window.pane.list.count() == 1)
    assert window.pane.list.item(0).data(Qt.ItemDataRole.UserRole)["id"] == code["id"]
    tag_entry = window.nav.findItems("自定义标签", Qt.MatchFlag.MatchExactly)[0]
    window.nav.setCurrentItem(tag_entry)
    window.tags.setCurrentIndex(window.tags.findData("课程"))
    until(app, lambda: not window.pane.busy and window.pane.list.count() == 1 and
          window.pane.list.item(0).data(Qt.ItemDataRole.UserRole)["id"] == tagged["id"])
    assert window.heading.text() == "自定义标签"


def test_empty_favorites_and_no_search_result_then_return_to_history(themed_ui):
    app, repo, settings, jobs, window = themed_ui
    pane = window.pane
    pane.scope = {"favorite": True}
    pane.refresh()
    until(app, lambda: not pane.busy and pane.results_stack.currentWidget() is pane.empty)
    favorite_caption = " ".join(label.text() for label in pane.empty.findChildren(QLabel))
    assert "收藏" in favorite_caption
    pane.search.setText("课程")
    until(app, lambda: not pane.timer.isActive() and not pane.busy and
          pane.results_stack.currentWidget() is pane.empty)
    search_caption = " ".join(label.text() for label in pane.empty.findChildren(QLabel))
    assert search_caption != favorite_caption
    assert "匹配" in search_caption or "找到" in search_caption
    record = repo.add_text("课程资料")
    repo.update(record["id"], favorite=True)
    pane.refresh()
    until(app, lambda: not pane.busy and pane.results_stack.currentWidget() is pane.list)
    assert pane.list.count() == 1
    assert pane.list.item(0).data(Qt.ItemDataRole.UserRole)["id"] == record["id"]


def test_quick_chips_preserve_other_filters_and_do_not_copy_or_paste(themed_ui):
    app, repo, settings, jobs, window = themed_ui
    code = repo.add_text("def greeting():\n    return 'hello'\n")
    repo.update(code["id"], pinned=True)
    favorite = repo.add_text("课程资料")
    repo.update(favorite["id"], favorite=True)
    quick = window.quick
    quick.summon()
    until(app, lambda: not quick.pane.busy and quick.pane.list.count() == 2)
    quick.chips["code"].click()
    until(app, lambda: not quick.pane.timer.isActive() and not quick.pane.busy and quick.pane.list.count() == 1)
    assert quick.pane.list.item(0).data(Qt.ItemDataRole.UserRole)["id"] == code["id"]
    quick.pane.time.setCurrentIndex(quick.pane.time.findData(7))
    quick.pane.pinned.setChecked(True)
    quick.chips["favorite"].click()
    assert quick.pane.favorite.isChecked() and quick.pane.kind.currentData() == ""
    assert quick.pane.time.currentData() == 7 and quick.pane.parameters()["pinned"] is True
    quick.pane.pinned.setChecked(False)
    until(app, lambda: not quick.pane.timer.isActive() and not quick.pane.busy and quick.pane.list.count() == 1)
    assert quick.pane.list.item(0).data(Qt.ItemDataRole.UserRole)["id"] == favorite["id"]
    quick.pane.favorite.setChecked(False)
    quick.pane.kind.setCurrentIndex(quick.pane.kind.findData("image"))
    assert quick.chips["image"].isChecked()
    assert quick.passive and not quick._paste_busy
    assert all(chip.focusPolicy() == Qt.FocusPolicy.NoFocus for chip in quick.chips.values())


def test_quick_context_copy_only_uses_window_callback_without_pasting(themed_ui):
    app, repo, settings, jobs, window = themed_ui
    record = repo.add_text("仅复制测试")
    quick = window.quick
    copied = []

    def fake_copy(item, done=None):
        copied.append(item["text"])
        if done:
            done()

    quick._copy = fake_copy
    quick.summon()
    until(app, lambda: not quick.pane.busy and quick.pane.list.count() == 1)
    rect = quick.pane.list.visualItemRect(quick.pane.list.item(0))
    quick.pane.context_menu(rect.center())
    try:
        action = next(action for action in quick.pane.context.actions() if action.text() == "仅复制")
        action.trigger()
        app.processEvents()
        assert copied == [record["text"]]
        assert not quick._paste_busy and quick.isVisible()
        assert "Ctrl+V" in quick.hint.text()
    finally:
        quick.pane.context.hide()


def test_no_result_reset_clears_sidebar_and_list_filters(themed_ui):
    app, repo, settings, jobs, window = themed_ui
    record = repo.add_text("课程记录")
    repo.update(record["id"], tags=["课程"])
    window.refresh()
    until(app, lambda: window.tags.findData("课程") >= 0)
    window.nav.setCurrentItem(window.nav.findItems("自定义标签", Qt.MatchFlag.MatchExactly)[0])
    window.tags.setCurrentIndex(window.tags.findData("课程"))
    pane = window.pane
    pane.search.setText("未匹配")
    pane.kind.setCurrentIndex(pane.kind.findData("code"))
    pane.time.setCurrentIndex(pane.time.findData(7))
    pane.favorite.setChecked(True)
    pane.pinned.setChecked(True)
    pane.refresh()
    until(app, lambda: not pane.busy and pane.results_stack.currentWidget() is pane.empty)
    pane.empty_button.click()
    until(app, lambda: not pane.busy and pane.results_stack.currentWidget() is pane.list)
    assert window.nav.currentItem().text() == "最近记录"
    assert window.tags.currentData() == "" and window.group.currentData() == ""
    assert pane.search.text() == "" and pane.kind.currentData() == "" and pane.time.currentData() == 0
    assert not pane.favorite.isChecked() and not pane.pinned.isChecked()
    assert pane.list.count() == 1
    assert pane.list.item(0).data(Qt.ItemDataRole.UserRole)["id"] == record["id"]
