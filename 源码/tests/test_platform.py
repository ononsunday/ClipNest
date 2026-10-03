"""系统层回归；默认剪贴板测试使用内存替身，不触碰个人剪贴板。"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QMimeData, QObject, Signal, Qt
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtWidgets import QApplication, QWidget

from clipnest.platform.settings import DEFAULTS, Settings
from clipnest.platform import windows
from clipnest.platform.clipboard import ClipboardMonitor, MAX_TEXT_CHARACTERS


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_settings_defaults_atomic_roundtrip(tmp_path):
    settings = Settings(tmp_path)
    assert settings.get("max_items") == 2000
    assert settings.get("theme") == "light"
    settings.set("theme", "dark")
    settings.set("excluded_apps", ["KEEPass.exe", "keepass.exe"])
    settings.save()
    assert Settings(tmp_path).get("theme") == "dark"
    assert Settings(tmp_path).get("excluded_apps") == ["keepass.exe"]
    assert list(tmp_path.glob("*.tmp")) == []
    mutable = settings.get("excluded_apps")
    mutable.append("evil.exe")
    assert settings.get("excluded_apps") == ["keepass.exe"]


@pytest.mark.parametrize("key,value", [("theme", "purple"), ("max_items", 0),
    ("max_items", True), ("max_age_days", -1), ("startup", 1),
    ("excluded_apps", [r"C:\keepass.exe"]), ("excluded_apps", ["chrome"]),
    ("hotkey", ""), ("secret", "x")])
def test_invalid_settings_rejected(tmp_path, key, value):
    settings = Settings(tmp_path)
    with pytest.raises(ValueError):
        settings.set(key, value)
    assert settings.values == DEFAULTS


def test_corrupt_settings_preserved(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("not json", encoding="utf-8")
    settings = Settings(tmp_path)
    assert settings.load_warning
    assert settings.get("max_age_days") == 30
    assert path.read_text(encoding="utf-8") == "not json"


def test_partially_invalid_settings_use_valid_values(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"theme": "dark", "max_items": -1}), encoding="utf-8")
    settings = Settings(tmp_path)
    assert settings.get("theme") == "dark"
    assert settings.get("max_items") == 2000
    assert settings.load_warning


def test_settings_atomic_failure_keeps_previous_file(tmp_path, monkeypatch):
    settings = Settings(tmp_path)
    settings.save()
    previous = settings.path.read_bytes()
    settings.set("theme", "dark")
    def fail_replace(*args):
        raise PermissionError("synthetic occupied settings file")
    monkeypatch.setattr("clipnest.platform.settings.os.replace", fail_replace)
    with pytest.raises(PermissionError):
        settings.save()
    assert settings.path.read_bytes() == previous
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.parametrize("position", [None, [0, 0], [-1920, 120], [100000, -100000]])
def test_quick_position_roundtrip_preserves_other_settings(tmp_path, position):
    settings = Settings(tmp_path)
    settings.set("theme", "dark")
    settings.set("max_items", 321)
    settings.set("quick_position", position)
    settings.save()
    reloaded = Settings(tmp_path)
    assert reloaded.get("quick_position") == position
    assert reloaded.get("theme") == "dark"
    assert reloaded.get("max_items") == 321
    if position is not None:
        reloaded.get("quick_position")[0] = 1
        assert reloaded.get("quick_position") == position


@pytest.mark.parametrize("position", [[], [1], [1, 2, 3], (1, 2), "1,2",
    [True, 2], [1, False], [1.0, 2], [1, "2"], [100001, 0], [0, -100001]])
def test_quick_position_rejects_malformed_values(tmp_path, position):
    settings = Settings(tmp_path)
    with pytest.raises(ValueError):
        settings.set("quick_position", position)
    assert settings.get("quick_position") is None


def test_bad_saved_quick_position_keeps_valid_settings_and_file(tmp_path):
    path = tmp_path / "settings.json"
    contents = json.dumps({"theme": "dark", "quick_position": [True, 4]})
    path.write_text(contents, encoding="utf-8")
    settings = Settings(tmp_path)
    assert settings.get("theme") == "dark"
    assert settings.get("quick_position") is None
    assert settings.load_warning
    assert path.read_text(encoding="utf-8") == contents


def test_close_after_paste_defaults_and_roundtrip(tmp_path):
    settings = Settings(tmp_path)
    assert settings.get("close_after_paste") is False
    settings.set("theme", "dark")
    settings.set("quick_position", [-1920, 300])
    settings.set("close_after_paste", True)
    settings.save()
    loaded = Settings(tmp_path)
    assert loaded.get("close_after_paste") is True
    assert loaded.get("theme") == "dark"
    assert loaded.get("quick_position") == [-1920, 300]
    loaded.set("close_after_paste", False)
    loaded.save()
    assert Settings(tmp_path).get("close_after_paste") is False


@pytest.mark.parametrize("value", [0, 1, "true", "false", None, [], {}])
def test_close_after_paste_only_accepts_bool(tmp_path, value):
    settings = Settings(tmp_path)
    with pytest.raises(ValueError):
        settings.set("close_after_paste", value)
    assert settings.get("close_after_paste") is False


@pytest.fixture
def passive_window_native_mock(monkeypatch):
    calls = []
    attributes = []
    handle = 0x123456789  # 超过 32 位，确保 Python 句柄传递不会截断。
    state = {"style": 0x00080088, "error": 0}
    def get_style(hwnd, index):
        calls.append(("get", hwnd, index))
        return state["style"]
    def set_style(hwnd, index, style):
        calls.append(("set", hwnd, index, style))
        previous = state["style"]
        state["style"] = style
        return previous
    fake_user = SimpleNamespace(SetWindowPos=lambda *args: calls.append(("position", *args)) or True)
    widget = SimpleNamespace(winId=lambda: handle,
        setAttribute=lambda *args: attributes.append(args), isMinimized=lambda: False,
        show=lambda: calls.append(("show",)), showNormal=lambda: calls.append(("normal",)))
    monkeypatch.setattr(windows, "IS_WINDOWS", True)
    monkeypatch.setattr(windows, "_user32", fake_user)
    monkeypatch.setattr(windows, "_get_window_long_ptr", get_style)
    monkeypatch.setattr(windows, "_set_window_long_ptr", set_style)
    monkeypatch.setattr(ctypes, "set_last_error", lambda value: state.update(error=value), raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: state["error"], raising=False)
    return widget, calls, attributes, state, handle


def test_passive_style_preserves_flags_and_64bit_handle(passive_window_native_mock):
    widget, calls, attributes, state, handle = passive_window_native_mock
    original = state["style"]
    assert windows.set_window_no_activate(widget, True)
    assert state["style"] == original | windows.WS_EX_NOACTIVATE
    assert attributes[-1] == (Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    assert calls[1] == ("set", handle, windows.GWL_EXSTYLE, original | windows.WS_EX_NOACTIVATE)
    assert calls[-1][-1] & windows.SWP_NOACTIVATE
    assert calls[-1][-1] & windows.SWP_NOZORDER
    assert calls[-1][-1] & windows.SWP_FRAMECHANGED
    assert windows.set_window_no_activate(widget, False)
    assert state["style"] == original
    assert attributes[-1] == (Qt.WidgetAttribute.WA_ShowWithoutActivating, False)


def test_passive_topmost_show_never_requests_activation(passive_window_native_mock):
    widget, calls, _, _, handle = passive_window_native_mock
    assert windows.show_no_activate_topmost(widget)
    assert ("show",) in calls
    assert calls[-1][0:3] == ("position", handle, windows.HWND_TOPMOST)
    assert calls[-1][-1] & windows.SWP_NOACTIVATE
    assert calls[-1][-1] & windows.SWP_SHOWWINDOW
    assert not calls[-1][-1] & windows.SWP_NOZORDER


def test_zero_previous_style_is_success_without_last_error(passive_window_native_mock):
    widget, _, _, state, _ = passive_window_native_mock
    state["style"] = 0
    assert windows.set_window_no_activate(widget, True)
    assert state["style"] == windows.WS_EX_NOACTIVATE


def test_failed_native_style_reports_false_without_show_activation(passive_window_native_mock, monkeypatch):
    widget, calls, _, state, _ = passive_window_native_mock
    def failure(*args):
        state["error"] = 5
        return 0
    monkeypatch.setattr(windows, "_set_window_long_ptr", failure)
    assert windows.set_window_no_activate(widget, True) is False
    assert not any(call[0] == "position" for call in calls)


def test_foreground_restore_rejects_dead_or_invalid_handles(monkeypatch):
    restored = []
    fake_user = SimpleNamespace(GetForegroundWindow=lambda: 0x123456789,
        IsWindow=lambda handle: handle == 0x123456789,
        SetForegroundWindow=lambda handle: restored.append(handle) or True)
    monkeypatch.setattr(windows, "IS_WINDOWS", True)
    monkeypatch.setattr(windows, "_user32", fake_user)
    assert windows.foreground_window() == 0x123456789
    for handle in (None, 0, -1, True, 123):
        assert windows.restore_foreground(handle) is False
    assert restored == []
    assert windows.restore_foreground(0x123456789)
    assert restored == [0x123456789]


@pytest.mark.parametrize("key_state,down", [(0, False), (1, False),
    (0x8000, True), (0x8001, True), (-32768, True)])
def test_left_button_observation_uses_current_high_bit(monkeypatch, key_state, down):
    queried = []
    def get_state(key):
        queried.append(key)
        return key_state
    monkeypatch.setattr(windows, "IS_WINDOWS", True)
    monkeypatch.setattr(windows, "_user32", SimpleNamespace(GetAsyncKeyState=get_state))
    assert windows.left_button_down() is down
    assert queried == [0x01]


def test_left_button_observation_unsupported_platform_does_not_call_native(monkeypatch):
    monkeypatch.setattr(windows, "IS_WINDOWS", False)
    monkeypatch.setattr(windows, "_user32", None)
    assert windows.left_button_down() is False


@pytest.fixture
def paste_native_mock(monkeypatch):
    """完整替代 Win32 输入接口，不发送按键、不读取或修改剪贴板。"""
    target = 0x123456789 if ctypes.sizeof(ctypes.c_void_p) == 8 else 0x12345678
    state = {"target": target, "foreground": target, "valid": True,
        "held": set(), "sent": 4, "error": 0, "send_error": 0}
    calls = []
    def send_input(count, inputs, size):
        events = [(inputs[index].type, inputs[index].ki.wVk, inputs[index].ki.wScan,
            inputs[index].ki.dwFlags, inputs[index].ki.time, inputs[index].ki.dwExtraInfo)
            for index in range(count)]
        calls.append((count, events, size))
        state["error"] = state["send_error"]
        return state["sent"] if len(calls) == 1 else count
    fake_user = SimpleNamespace(IsWindow=lambda handle: state["valid"] and handle == target,
        GetForegroundWindow=lambda: state["foreground"],
        GetAsyncKeyState=lambda key: 0x8000 if key in state["held"] else 0,
        SendInput=send_input)
    monkeypatch.setattr(windows, "IS_WINDOWS", True)
    monkeypatch.setattr(windows, "_user32", fake_user)
    monkeypatch.setattr(ctypes, "set_last_error", lambda value: state.update(error=value), raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: state["error"], raising=False)
    return target, state, calls, fake_user


def test_paste_input_abi_contains_full_union_and_pointer_width():
    wide = ctypes.sizeof(ctypes.c_void_p) == 8
    assert ctypes.sizeof(windows._INPUT) == (40 if wide else 28)
    assert ctypes.sizeof(windows._KEYBDINPUT) == (24 if wide else 16)
    assert windows._INPUT.data.offset == (8 if wide else 4)
    assert windows._KEYBDINPUT.dwExtraInfo.offset == (16 if wide else 12)


def test_paste_input_sends_one_complete_ctrl_v_sequence(paste_native_mock):
    target, _, calls, _ = paste_native_mock
    ok, message = windows.paste_to_window(target)
    assert ok and message == "已发送粘贴快捷键"
    assert len(calls) == 1
    count, events, size = calls[0]
    assert count == 4 and size == ctypes.sizeof(windows._INPUT)
    assert events == [(1, 0x11, 0, 0, 0, 0), (1, 0x56, 0, 0, 0, 0),
        (1, 0x56, 0, 2, 0, 0), (1, 0x11, 0, 2, 0, 0)]


@pytest.mark.parametrize("handle", [None, True, 0, -1, 123, 1 << 80])
def test_paste_invalid_target_never_sends_input(paste_native_mock, handle):
    _, _, calls, _ = paste_native_mock
    assert windows.window_is_valid(handle) is False
    ok, message = windows.paste_to_window(handle)
    assert not ok and "窗口" in message
    assert calls == []


def test_paste_dead_target_never_sends_input(paste_native_mock):
    target, state, calls, _ = paste_native_mock
    state["valid"] = False
    ok, message = windows.paste_to_window(target)
    assert not ok and "已关闭" in message
    assert calls == []


@pytest.mark.parametrize("key", [0x10, 0x11, 0x12, 0x5B, 0x5C])
def test_paste_held_modifiers_refuse_without_releasing_user_keys(paste_native_mock, key):
    target, state, calls, _ = paste_native_mock
    state["held"].add(key)
    assert windows.modifiers_down()
    ok, message = windows.paste_to_window(target)
    assert not ok and "松开" in message
    assert calls == []
    assert state["held"] == {key}


def test_paste_modifier_recent_press_bit_does_not_block(paste_native_mock):
    target, _, _, fake = paste_native_mock
    fake.GetAsyncKeyState = lambda key: 1
    assert windows.modifiers_down() is False
    assert windows.paste_to_window(target)[0]


@pytest.mark.parametrize("held", [{0x0D}, {0x10}, {0x11}, {0x12}, {0x5B}, {0x5C}, {0x0D, 0x11}])
def test_paste_gesture_waits_for_enter_or_modifier_release(paste_native_mock, held):
    _, state, calls, _ = paste_native_mock
    state["held"] = held
    assert windows.paste_gesture_down() is True
    assert calls == []
    state["held"] = set()
    assert windows.paste_gesture_down() is False


def test_paste_gesture_recent_enter_bit_does_not_block(paste_native_mock):
    _, _, calls, fake = paste_native_mock
    fake.GetAsyncKeyState = lambda key: 1
    assert windows.paste_gesture_down() is False
    assert calls == []


def test_paste_current_foreground_must_match_target(paste_native_mock):
    target, state, calls, _ = paste_native_mock
    state["foreground"] = target + 1
    ok, message = windows.paste_to_window(target)
    assert not ok and "焦点" in message
    assert calls == []


def test_paste_rechecks_foreground_after_modifier_query(paste_native_mock):
    target, state, calls, fake = paste_native_mock
    def changed_foreground(key):
        state["foreground"] = target + 1
        return 0
    fake.GetAsyncKeyState = changed_foreground
    assert windows.paste_to_window(target)[0] is False
    assert calls == []


@pytest.mark.parametrize("error", [0, 5])
def test_paste_zero_sent_reports_possible_uipi_without_claiming_success(paste_native_mock, error):
    target, state, calls, _ = paste_native_mock
    state["sent"] = 0
    state["send_error"] = error
    ok, message = windows.paste_to_window(target)
    assert not ok
    assert "可能" in message and "权限" in message and f"错误 {error}" in message
    assert len(calls) == 1


@pytest.mark.parametrize("sent,release_keys", [(1, [0x11]), (2, [0x56, 0x11]), (3, [0x11])])
def test_paste_partial_send_releases_keys_without_repeating_v_down(paste_native_mock, sent, release_keys):
    target, state, calls, _ = paste_native_mock
    state["sent"] = sent
    state["send_error"] = 5
    ok, message = windows.paste_to_window(target)
    assert not ok and f"{sent}/4" in message and "错误 5" in message
    assert len(calls) == 2
    assert [event[1] for event in calls[1][1]] == release_keys
    assert all(event[3] == windows.KEYEVENTF_KEYUP for event in calls[1][1])


def test_paste_partial_send_releases_own_injected_key_even_if_foreground_changes(paste_native_mock):
    target, state, calls, fake = paste_native_mock
    state["sent"] = 1
    original = fake.SendInput
    def send_then_switch(*args):
        result = original(*args)
        state["foreground"] = target + 1
        return result
    fake.SendInput = send_then_switch
    assert windows.paste_to_window(target)[0] is False
    assert len(calls) == 2
    assert calls[1][1] == [(1, 0x11, 0, windows.KEYEVENTF_KEYUP, 0, 0)]


def test_paste_unsupported_platform_never_calls_native(monkeypatch):
    monkeypatch.setattr(windows, "IS_WINDOWS", False)
    monkeypatch.setattr(windows, "_user32", None)
    assert windows.modifiers_down() is False
    assert windows.paste_gesture_down() is False
    assert windows.window_is_valid(1) is False
    ok, message = windows.paste_to_window(1)
    assert not ok and "Windows" in message


@pytest.mark.skipif(sys.platform != "win32", reason="Windows pointer-width API declarations")
def test_window_api_signatures_are_pointer_width():
    assert windows._get_window_long_ptr.argtypes == [wintypes.HWND, ctypes.c_int]
    assert windows._get_window_long_ptr.restype is ctypes.c_ssize_t
    assert windows._set_window_long_ptr.argtypes[-1] is ctypes.c_ssize_t
    assert windows._set_window_long_ptr.restype is ctypes.c_ssize_t
    assert windows._user32.SetWindowPos.argtypes[:2] == [wintypes.HWND, wintypes.HWND]
    assert windows._user32.GetAsyncKeyState.argtypes == [ctypes.c_int]
    assert windows._user32.GetAsyncKeyState.restype is ctypes.c_short
    assert windows._user32.SendInput.argtypes == [wintypes.UINT, ctypes.POINTER(windows._INPUT), ctypes.c_int]
    assert windows._user32.SendInput.restype is wintypes.UINT


@pytest.mark.parametrize("text,expected", [("Ctrl+Alt+V", (3, 86)),
    ("Shift+Win+F24", (12, 0x87)), ("Ctrl+Space", (2, 32)),
    ("Control+Alt+Down", (3, 40))])
def test_hotkey_parser(text, expected):
    assert windows.parse_hotkey(text) == expected


@pytest.mark.parametrize("text", ["V", "Ctrl+Ctrl+V", "Ctrl++", "Ctrl+F12", "Ctrl+未知", "Ctrl+F25", "Ctrl+Alt"])
def test_hotkey_parser_rejects_invalid(text):
    with pytest.raises(ValueError):
        windows.parse_hotkey(text)


def test_startup_registry_mocked(monkeypatch):
    calls = []
    class Key:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
    fake = SimpleNamespace(HKEY_CURRENT_USER=1, KEY_SET_VALUE=2, REG_SZ=3,
        CreateKeyEx=lambda *args: Key(),
        SetValueEx=lambda *args: calls.append(("set", args)),
        DeleteValue=lambda *args: calls.append(("delete", args)))
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(windows, "IS_WINDOWS", True)
    windows.set_startup(True)
    assert calls[0][0] == "set"
    assert calls[0][1][1] == "ClipNest"
    assert "--start-hidden" in calls[0][1][-1]
    assert "run_clipnest.py" in calls[0][1][-1] or getattr(sys, "frozen", False)
    windows.set_startup(False)
    assert calls[-1][0] == "delete"


class FakeClipboard(QObject):
    dataChanged = Signal()
    def __init__(self):
        super().__init__()
        self.mime = QMimeData()
        self.sequence = 1
        self.reads = 0
    def setMimeData(self, mime, mode=None):
        self.mime = mime
        self.sequence += 1
        self.dataChanged.emit()
    def mimeData(self, mode=None):
        self.reads += 1
        return self.mime


@pytest.fixture
def monitor(qapp, tmp_path, monkeypatch):
    fake = FakeClipboard()
    monkeypatch.setattr(QGuiApplication, "clipboard", lambda: fake)
    monkeypatch.setattr(windows, "IS_WINDOWS", True)
    monkeypatch.setattr(windows, "clipboard_sequence", lambda: fake.sequence)
    monkeypatch.setattr(windows, "foreground_process", lambda: "notepad.exe")
    monkeypatch.setattr(windows, "clipboard_owner_process", lambda: "notepad.exe")
    monkeypatch.setattr(windows, "can_capture_sensitive_flags", lambda: True)
    instance = ClipboardMonitor(Settings(tmp_path))
    instance.start()
    yield instance, fake
    instance.stop()


def test_monitor_does_not_read_on_start(monitor):
    _, fake = monitor
    assert fake.reads == 0


def test_monitor_raw_text_pause_and_stop(monitor):
    instance, fake = monitor
    captured = []
    instance.captured_text.connect(lambda text, source: captured.append((text, source)))
    mime = QMimeData()
    mime.setText("  print('ClipNest test')\n")
    fake.setMimeData(mime)
    assert captured == [("  print('ClipNest test')\n", "notepad.exe")]
    fake.dataChanged.emit()  # 相同 sequence 不重复。
    assert len(captured) == 1
    instance.set_paused(True)
    fake.setMimeData(mime)
    assert len(captured) == 1
    instance.set_paused(False)
    assert len(captured) == 1  # 恢复时不回读暂停期间的内容。
    instance.stop()
    fake.setMimeData(mime)
    assert len(captured) == 1


def test_own_writes_are_suppressed(monitor):
    instance, fake = monitor
    captured = []
    instance.captured_text.connect(lambda *args: captured.append(args))
    assert instance.copy_text("ClipNest synthetic own write")
    fake.dataChanged.emit()
    assert captured == []
    assert fake.mime.text() == "ClipNest synthetic own write"


def test_excluded_apps_and_protected_flags(monitor, monkeypatch):
    instance, fake = monitor
    captured = []
    instance.captured_text.connect(lambda *args: captured.append(args))
    mime = QMimeData()
    mime.setText("ClipNest synthetic protected test")
    monkeypatch.setattr(windows, "clipboard_owner_process", lambda: "keepass.exe")
    fake.setMimeData(mime)
    assert captured == []
    monkeypatch.setattr(windows, "clipboard_owner_process", lambda: "notepad.exe")
    monkeypatch.setattr(windows, "can_capture_sensitive_flags", lambda: False)
    fake.setMimeData(mime)
    assert captured == []


def test_image_snapshot_and_copy(monitor):
    instance, fake = monitor
    captured = []
    instance.captured_image.connect(lambda image, source: captured.append(image))
    image = QImage(4, 4, QImage.Format.Format_ARGB32)
    image.fill(0xFFAABBCC)
    mime = QMimeData()
    mime.setImageData(image)
    fake.setMimeData(mime)
    image.fill(0xFF000000)
    assert captured[0].pixel(0, 0) == 0xFFAABBCC
    assert instance.copy_image(captured[0])
    assert len(captured) == 1
    assert fake.mime.imageData().pixel(0, 0) == 0xFFAABBCC


def test_text_limit_reports_error(monitor):
    instance, fake = monitor
    errors = []
    captured = []
    instance.error.connect(errors.append)
    instance.captured_text.connect(lambda *args: captured.append(args))
    mime = QMimeData()
    mime.setText("x" * (MAX_TEXT_CHARACTERS + 1))
    fake.setMimeData(mime)
    assert errors and captured == []


def test_clipboard_busy_retry_is_bounded(monitor, monkeypatch):
    instance, fake = monitor
    errors = []
    instance.error.connect(errors.append)
    monkeypatch.setattr(windows, "can_capture_sensitive_flags", lambda: None)
    mime = QMimeData()
    mime.setText("synthetic busy clipboard")
    fake.setMimeData(mime)
    for _ in range(5):
        instance._retry.stop()
        instance._capture()
    assert errors
    assert not instance._retry.isActive()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DWORD clipboard flag test")
def test_windows_history_protection_flags(monkeypatch):
    include = wintypes.DWORD(0)
    include_handle = 100
    formats = set()
    closed = []
    fake_user = SimpleNamespace(
        RegisterClipboardFormatW=lambda name: 1 if name.startswith("Exclude") else 2,
        OpenClipboard=lambda _: True,
        CloseClipboard=lambda: closed.append(True),
        IsClipboardFormatAvailable=lambda fmt: fmt in formats,
        GetClipboardData=lambda fmt: include_handle)
    fake_kernel = SimpleNamespace(GlobalSize=lambda handle: ctypes.sizeof(include),
        GlobalLock=lambda handle: ctypes.addressof(include), GlobalUnlock=lambda handle: True)
    monkeypatch.setattr(windows, "_user32", fake_user)
    monkeypatch.setattr(windows, "_kernel32", fake_kernel)
    assert windows.can_capture_sensitive_flags() is True
    formats.add(2)
    assert windows.can_capture_sensitive_flags() is False
    include.value = 1
    assert windows.can_capture_sensitive_flags() is True
    formats.add(1)
    assert windows.can_capture_sensitive_flags() is False
    assert len(closed) == 4
    fake_user.OpenClipboard = lambda _: False
    assert windows.can_capture_sensitive_flags() is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native hotkey test")
def test_windows_hotkey_register_conflict_preserves_old(qapp):
    first, second = windows.HotkeyManager(), windows.HotkeyManager()
    try:
        ok, reason = first.register("Ctrl+Alt+Shift+F23")
        assert ok, reason
        ok, reason = second.register("Ctrl+Alt+Shift+F24")
        assert ok, reason
        old_id = second._id
        ok, reason = second.register("Ctrl+Alt+Shift+F23")
        assert not ok and reason
        assert second._id == old_id
        assert second._registered == windows.parse_hotkey("Ctrl+Alt+Shift+F24")
        ok, reason = second.register("Ctrl+F12")
        assert not ok and second._id == old_id
    finally:
        first.unregister()
        second.unregister()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native dispatcher test")
def test_windows_hotkey_native_dispatch(qapp):
    manager = windows.HotkeyManager()
    notifications = []
    manager.triggered.connect(lambda: notifications.append(True))
    try:
        ok, reason = manager.register("Ctrl+Alt+Shift+F23")
        assert ok, reason
        post = windows._user32.PostThreadMessageW
        post.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        post.restype = wintypes.BOOL
        assert post(windows._kernel32.GetCurrentThreadId(), windows.WM_HOTKEY, manager._id, 0)
        qapp.processEvents()
        assert notifications == [True]
    finally:
        manager.unregister()


@pytest.mark.skipif(sys.platform != "win32" or os.getenv("CLIPNEST_NATIVE_TESTS") != "1",
                    reason="Opt-in Windows keyboard delivery test")
def test_windows_hotkey_from_real_key_events(qapp):
    """仅在自己的测试窗口前台时发送已注册组合键，不输入文字或自动粘贴。"""
    from PySide6.QtTest import QTest
    get_key = windows._user32.GetAsyncKeyState
    get_key.argtypes = [ctypes.c_int]
    get_key.restype = ctypes.c_short
    if any(get_key(vk) & 0x8000 for vk in (0x10, 0x11, 0x12, 0x5B, 0x5C)):
        pytest.skip("用户正在按修饰键，跳过键盘事件测试")
    manager = windows.HotkeyManager()
    notifications = []
    manager.triggered.connect(lambda: notifications.append(True))
    previous = windows._user32.GetForegroundWindow()
    window = QWidget()
    window.setWindowTitle("ClipNest 原生快捷键验证")
    window.resize(360, 160)
    keys = (0x11, 0x12, 0x10, 0x78)  # Ctrl + Alt + Shift + F9
    key_event = windows._user32.keybd_event
    key_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]
    key_event.restype = None
    sent = False
    try:
        ok, reason = manager.register("Ctrl+Alt+Shift+F9")
        if not ok:
            pytest.skip(reason)
        windows.bring_to_front(window)
        QTest.qWait(80)
        if windows._user32.GetForegroundWindow() != int(window.winId()):
            pytest.skip("Windows 未允许测试窗口获得前台，未发送按键")
        for vk in keys:
            key_event(vk, 0, 0, 0)
        sent = True
        for vk in reversed(keys):
            key_event(vk, 0, 2, 0)  # KEYEVENTF_KEYUP
        sent = False
        for _ in range(20):
            QTest.qWait(25)
            if notifications:
                break
        assert notifications == [True]
    finally:
        if sent:
            for vk in reversed(keys):
                key_event(vk, 0, 2, 0)
        manager.unregister()
        window.close()
        if previous:
            windows._user32.SetForegroundWindow(previous)


@pytest.mark.skip(reason="真实剪贴板测试暂禁：OLE 恢复路径曾引发 ole32.dll/0xc000041d 崩溃，原剪贴板恢复无法确认")
def test_windows_real_clipboard_text_and_image(qapp, tmp_path, monkeypatch):
    """用 OLE 接口指针暂存原对象；不读取、检查或输出原剪贴板内容。"""
    # WinDLL 返回 HRESULT 原值，清理阶段失败时仍然能够重试和释放指针。
    ole = ctypes.WinDLL("ole32")
    ole.OleGetClipboard.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
    ole.OleGetClipboard.restype = ctypes.c_long
    ole.OleSetClipboard.argtypes = [ctypes.c_void_p]
    ole.OleSetClipboard.restype = ctypes.c_long
    ole.OleFlushClipboard.argtypes = []
    ole.OleFlushClipboard.restype = ctypes.c_long
    original = ctypes.c_void_p()
    assert ole.OleGetClipboard(ctypes.byref(original)) == 0
    instance = ClipboardMonitor(Settings(tmp_path))
    monkeypatch.setattr(windows, "foreground_process", lambda: "clipnest-test.exe")
    monkeypatch.setattr(windows, "clipboard_owner_process", lambda: "clipnest-test.exe")
    text_records, image_records = [], []
    instance.captured_text.connect(lambda text, source: text_records.append(text))
    instance.captured_image.connect(lambda image, source: image_records.append(image))
    instance.start()
    try:
        clipboard = QGuiApplication.clipboard()
        text = "ClipNest 本地原生合成测试\n  x = 1\n"
        clipboard.setText(text)
        qapp.processEvents()
        assert text_records == [text]
        assert instance.copy_text(text)
        qapp.processEvents()
        assert text_records == [text]
        protected = QMimeData()
        protected.setText("ClipNest synthetic protected content")
        protected.setData('application/x-qt-windows-mime;value="CanIncludeInClipboardHistory"', bytes(4))
        clipboard.setMimeData(protected)
        qapp.processEvents()
        assert windows.can_capture_sensitive_flags() is False
        assert text_records == [text]
        protected = QMimeData()
        protected.setText("ClipNest synthetic monitor-excluded content")
        protected.setData('application/x-qt-windows-mime;value="ExcludeClipboardContentFromMonitorProcessing"', b"1")
        clipboard.setMimeData(protected)
        qapp.processEvents()
        assert windows.can_capture_sensitive_flags() is False
        assert text_records == [text]
        image = QImage(16, 12, QImage.Format.Format_ARGB32)
        image.fill(0xFFAADDFF)
        clipboard.setImage(image)
        qapp.processEvents()
        assert len(image_records) == 1
        assert image_records[0].size() == image.size()
        assert instance.copy_image(image)
        qapp.processEvents()
        assert len(image_records) == 1
        assert clipboard.image().pixel(0, 0) == 0xFFAADDFF
    finally:
        instance.stop()
        from PySide6.QtTest import QTest
        result = -1
        flush = -1
        try:
            for _ in range(10):
                qapp.processEvents()
                result = ole.OleSetClipboard(original)
                if result == 0:
                    break
                QTest.qWait(50)
            if result == 0:
                # 必须渲染并释放 OLE 迟延对象，测试进程退出后内容才仍可粘贴。
                for _ in range(10):
                    flush = ole.OleFlushClipboard()
                    if flush == 0:
                        break
                    QTest.qWait(50)
        finally:
            if original:
                # IDataObject 继承 IUnknown；只调用 Release，不读取其中内容。
                table = ctypes.cast(original, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
                release = ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)(table[2])
                release(original)
        assert result == 0, f"测试结束时无法恢复原剪贴板对象：HRESULT {result}"
        assert flush == 0, f"测试结束时无法保留恢复的剪贴板：HRESULT {flush}"
