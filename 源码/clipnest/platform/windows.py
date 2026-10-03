"""集中封装 Win32；所有指针接口明确声明，兼容 64 位 Python。"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import itertools
from pathlib import Path
import re
import subprocess
import sys

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QObject, Signal, Qt

IS_WINDOWS = sys.platform == "win32"
WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000
GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_SHOWWINDOW = 0x0040
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_V = 0x56
_ids = itertools.count(0x4E00)


# INPUT 的 union 必须包含三种结构；只放键盘字段会导致 cbSize 错误。
# 使用固定宽度的 Windows 整数和指针宽度 ULONG_PTR，便于跨平台模拟测试。
class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_int32), ("dy", ctypes.c_int32),
        ("mouseData", ctypes.c_uint32), ("dwFlags", ctypes.c_uint32),
        ("time", ctypes.c_uint32), ("dwExtraInfo", ctypes.c_size_t)]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_uint16), ("wScan", ctypes.c_uint16),
        ("dwFlags", ctypes.c_uint32), ("time", ctypes.c_uint32),
        ("dwExtraInfo", ctypes.c_size_t)]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", ctypes.c_uint32), ("wParamL", ctypes.c_uint16),
        ("wParamH", ctypes.c_uint16)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("type", ctypes.c_uint32), ("data", _INPUTUNION)]


if IS_WINDOWS:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    _user32.RegisterHotKey.restype = wintypes.BOOL
    _user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.UnregisterHotKey.restype = wintypes.BOOL
    _user32.GetForegroundWindow.argtypes = []
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    _user32.GetAsyncKeyState.restype = ctypes.c_short
    _user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
    _user32.SendInput.restype = wintypes.UINT
    _user32.IsWindow.argtypes = [wintypes.HWND]
    _user32.IsWindow.restype = wintypes.BOOL
    _user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    _user32.SetWindowPos.restype = wintypes.BOOL
    # LONG_PTR 随进程位数变化，32 位 Windows 使用对应的 Long 接口。
    _get_window_long_ptr = getattr(_user32, "GetWindowLongPtrW" if ctypes.sizeof(ctypes.c_void_p) == 8 else "GetWindowLongW")
    _set_window_long_ptr = getattr(_user32, "SetWindowLongPtrW" if ctypes.sizeof(ctypes.c_void_p) == 8 else "SetWindowLongW")
    _get_window_long_ptr.argtypes = [wintypes.HWND, ctypes.c_int]
    _get_window_long_ptr.restype = ctypes.c_ssize_t
    _set_window_long_ptr.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    _set_window_long_ptr.restype = ctypes.c_ssize_t
    _user32.GetClipboardOwner.argtypes = []
    _user32.GetClipboardOwner.restype = wintypes.HWND
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _user32.GetClipboardSequenceNumber.argtypes = []
    _user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
    _user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
    _user32.RegisterClipboardFormatW.restype = wintypes.UINT
    _user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
    _user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
    _user32.OpenClipboard.argtypes = [wintypes.HWND]
    _user32.OpenClipboard.restype = wintypes.BOOL
    _user32.CloseClipboard.argtypes = []
    _user32.CloseClipboard.restype = wintypes.BOOL
    _user32.GetClipboardData.argtypes = [wintypes.UINT]
    _user32.GetClipboardData.restype = wintypes.HANDLE
    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _user32.SetForegroundWindow.restype = wintypes.BOOL
    _user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    _user32.AttachThreadInput.restype = wintypes.BOOL
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    _kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    _kernel32.GlobalLock.restype = ctypes.c_void_p
    _kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    _kernel32.GlobalUnlock.restype = wintypes.BOOL
    _kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]
    _kernel32.GlobalSize.restype = ctypes.c_size_t
    _kernel32.GetCurrentThreadId.argtypes = []
    _kernel32.GetCurrentThreadId.restype = wintypes.DWORD
else:
    _user32 = _kernel32 = None
    _get_window_long_ptr = _set_window_long_ptr = None


def parse_hotkey(sequence: str) -> tuple[int, int]:
    """验证组合键并转换为 Windows modifiers 和 virtual-key。"""
    parts = [part.strip().casefold() for part in sequence.split("+")]
    if not parts or any(not part for part in parts):
        raise ValueError("使用 Ctrl+Alt+V 这样的组合键格式")
    modifiers = {"ctrl": 2, "control": 2, "alt": 1, "shift": 4, "win": 8, "meta": 8}
    mask = 0
    for part in parts[:-1]:
        if part not in modifiers or mask & modifiers[part]:
            raise ValueError("修饰键只能是 Ctrl、Alt、Shift、Win，且不能重复")
        mask |= modifiers[part]
    if not mask:
        raise ValueError("全局快捷键至少需要一个修饰键")
    key = parts[-1]
    names = {"space": 0x20, "tab": 0x09, "esc": 0x1B, "escape": 0x1B,
             "enter": 0x0D, "return": 0x0D, "insert": 0x2D, "ins": 0x2D,
             "delete": 0x2E, "del": 0x2E, "home": 0x24, "end": 0x23,
             "pageup": 0x21, "pgup": 0x21, "pagedown": 0x22, "pgdown": 0x22,
             "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
             "backspace": 0x08}
    if len(key) == 1 and key.isascii() and key.isalnum():
        vk = ord(key.upper())
    elif key in names:
        vk = names[key]
    elif re.fullmatch(r"f([1-9]|1[0-9]|2[0-4])", key):
        if key == "f12":
            raise ValueError("F12 是 Windows 调试器保留键，请选择其他快捷键")
        vk = 0x6F + int(key[1:])
    else:
        raise ValueError("支持字母、数字、F1–F24（F12 除外）、方向键及常用导航键")
    return mask, vk


class _HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, owner: "HotkeyManager"):
        super().__init__()
        self.owner = owner

    def nativeEventFilter(self, event_type, message):  # noqa: N802 - Qt 接口
        if IS_WINDOWS and bytes(event_type) in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.wParam == self.owner._id:
                self.owner.triggered.emit()
                return True, 0
        return False, 0


class HotkeyManager(QObject):
    triggered = Signal()

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._id: int | None = None
        self._registered: tuple[int, int] | None = None
        self._filter = _HotkeyFilter(self)
        self._installed = False

    def register(self, sequence: str) -> tuple[bool, str]:
        try:
            key = parse_hotkey(sequence)
        except (ValueError, AttributeError) as exc:
            return False, str(exc)
        if not IS_WINDOWS:
            return False, "全局快捷键需要 Windows；仍可通过托盘打开窗口。"
        app = QCoreApplication.instance()
        if app is None:
            return False, "Qt 应用尚未初始化"
        if key == self._registered:
            return True, "快捷键已经注册"
        new_id = next(_ids)
        if new_id >= 0xBFFF:
            return False, "快捷键注册次数过多，请重启 ClipNest"
        # 先注册新键，再释放旧键，设置失败不会丢失原快捷键。
        if not _user32.RegisterHotKey(None, new_id, key[0] | MOD_NOREPEAT, key[1]):
            code = ctypes.get_last_error()
            return False, f"快捷键无法注册，可能已被其他软件占用（Windows 错误 {code}）。请更换组合键。"
        old_id = self._id
        self._id, self._registered = new_id, key
        if not self._installed:
            app.installNativeEventFilter(self._filter)
            self._installed = True
        if old_id is not None:
            _user32.UnregisterHotKey(None, old_id)
        return True, "快捷键已启用"

    def unregister(self) -> None:
        if IS_WINDOWS and self._id is not None:
            _user32.UnregisterHotKey(None, self._id)
        self._id = None
        self._registered = None
        app = QCoreApplication.instance()
        if self._installed and app is not None:
            app.removeNativeEventFilter(self._filter)
        self._installed = False


def _process_for_window(handle) -> str:
    if not IS_WINDOWS or not handle:
        return ""
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
    process = _kernel32.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
    if not process:
        return ""
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buffer))
        if _kernel32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
            return Path(buffer.value).name.casefold()
        return ""
    finally:
        _kernel32.CloseHandle(process)


def foreground_process() -> str:
    return _process_for_window(_user32.GetForegroundWindow()) if IS_WINDOWS else ""


def foreground_window() -> int:
    """只取得前台窗口句柄，不读取应用文本或移动其输入光标。"""
    return int(_user32.GetForegroundWindow() or 0) if IS_WINDOWS else 0


def left_button_down() -> bool:
    """仅观察当前鼠标左键状态；不捕获、吞掉或生成任何输入事件。"""
    # 只使用高位的当前按下状态，低位的“最近按过”状态不可靠。
    return bool(_user32.GetAsyncKeyState(0x01) & 0x8000) if IS_WINDOWS else False


def window_is_valid(handle: int) -> bool:
    """校验仍存在的窗口，避免将键盘输入发送到过期或截断的句柄。"""
    if (not IS_WINDOWS or isinstance(handle, bool) or not isinstance(handle, int)
            or not 0 < handle < 1 << (ctypes.sizeof(ctypes.c_void_p) * 8)):
        return False
    return bool(_user32.IsWindow(handle))


def modifiers_down() -> bool:
    """等待现有 Ctrl/Alt/Shift/Win 全部松开，不改变用户按键状态。"""
    return IS_WINDOWS and any(_user32.GetAsyncKeyState(key) & 0x8000
        for key in (0x10, VK_CONTROL, 0x12, 0x5B, 0x5C))


def paste_gesture_down() -> bool:
    """粘贴前等待触发用的 Enter 和修饰键松开，避免目标收到长按回车。"""
    return modifiers_down() or (IS_WINDOWS and bool(_user32.GetAsyncKeyState(0x0D) & 0x8000))


def _keyboard_event(key: int, flags: int = 0) -> _INPUT:
    event = _INPUT()
    event.type = INPUT_KEYBOARD
    event.ki.wVk = key
    event.ki.dwFlags = flags
    return event


def paste_to_window(handle: int) -> tuple[bool, str]:
    """向已回到前台的目标发送一次 Ctrl+V；不切焦点或读取剪贴板。"""
    if not IS_WINDOWS:
        return False, "自动粘贴仅支持 Windows"
    if not window_is_valid(handle):
        return False, "原输入窗口已关闭；请重新点击需要粘贴的输入框"
    if modifiers_down():
        return False, "请先松开 Ctrl、Alt、Shift 和 Win，再尝试粘贴"
    inputs = (_INPUT * 4)(_keyboard_event(VK_CONTROL), _keyboard_event(VK_V),
        _keyboard_event(VK_V, KEYEVENTF_KEYUP), _keyboard_event(VK_CONTROL, KEYEVENTF_KEYUP))
    # 在真正发送前再次确认目标前台，避免异步复制期间用户切换窗口后误粘贴。
    if foreground_window() != handle:
        return False, "目标输入窗口尚未获得焦点；请点击输入框后重试"
    ctypes.set_last_error(0)
    sent = int(_user32.SendInput(len(inputs), inputs, ctypes.sizeof(_INPUT)))
    if sent == len(inputs):
        return True, "已发送粘贴快捷键"
    error = ctypes.get_last_error()
    if 0 < sent < len(inputs):
        # 部分发送只补必要的释放键，绝不重发 V↓，避免重复粘贴。
        # 键盘状态是全局的，即便前台刚切换也需释放本批注入的按下键。
        releases = [_keyboard_event(VK_V, KEYEVENTF_KEYUP)] if sent == 2 else []
        releases.append(_keyboard_event(VK_CONTROL, KEYEVENTF_KEYUP))
        cleanup = (_INPUT * len(releases))(*releases)
        _user32.SendInput(len(cleanup), cleanup, ctypes.sizeof(_INPUT))
        return False, f"粘贴按键未完整发送（{sent}/4；Windows 错误 {error}）；请检查目标内容，若 Ctrl 状态异常请按下再松开 Ctrl"
    # UIPI 阻断不一定有错误码，不能声称已经检测到管理员权限问题。
    return False, f"Windows 未接收粘贴快捷键（错误 {error}）；可能被更高权限的软件或系统策略阻止，可在原软件手动 Ctrl+V"


def restore_foreground(handle: int) -> bool:
    """显式搜索结束后交还前台；无效句柄不操作，不合并线程输入队列。"""
    if not window_is_valid(handle):
        return False
    return bool(_user32.SetForegroundWindow(handle))


def set_window_no_activate(widget, enabled: bool) -> bool:
    """显示、点击被动浮窗时不抢焦点；保留 Qt 已设置的其他扩展样式。"""
    widget.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, enabled)
    if not IS_WINDOWS:
        return True
    handle = int(widget.winId())
    ctypes.set_last_error(0)
    style = int(_get_window_long_ptr(handle, GWL_EXSTYLE))
    if not style and ctypes.get_last_error():
        return False
    updated = style | WS_EX_NOACTIVATE if enabled else style & ~WS_EX_NOACTIVATE
    if updated == style:
        return True
    ctypes.set_last_error(0)
    previous = _set_window_long_ptr(handle, GWL_EXSTYLE, updated)
    # 零既可能是旧样式，也可能是失败，必须结合 last-error 判断。
    if not previous and ctypes.get_last_error():
        return False
    return bool(_user32.SetWindowPos(handle, None, 0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED))


def show_no_activate_topmost(widget) -> bool:
    """浮窗保持最上层，同时让原应用继续接收键盘输入。"""
    configured = set_window_no_activate(widget, True)
    if widget.isMinimized():
        widget.showNormal()
    else:
        widget.show()
    if not IS_WINDOWS:
        return configured
    shown = bool(_user32.SetWindowPos(int(widget.winId()), HWND_TOPMOST, 0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW))
    return configured and shown


def clipboard_owner_process() -> str:
    return _process_for_window(_user32.GetClipboardOwner()) if IS_WINDOWS else ""


def clipboard_sequence() -> int:
    return int(_user32.GetClipboardSequenceNumber()) if IS_WINDOWS else 0


def can_capture_sensitive_flags() -> bool | None:
    """False 表示生产应用要求不留历史；None 表示暂时无法安全读取。"""
    if not IS_WINDOWS:
        return True
    exclude = _user32.RegisterClipboardFormatW("ExcludeClipboardContentFromMonitorProcessing")
    include = _user32.RegisterClipboardFormatW("CanIncludeInClipboardHistory")
    if not _user32.OpenClipboard(None):
        return None
    try:
        if _user32.IsClipboardFormatAvailable(exclude):
            return False
        if not _user32.IsClipboardFormatAvailable(include):
            return True
        data = _user32.GetClipboardData(include)
        if not data or _kernel32.GlobalSize(data) < ctypes.sizeof(wintypes.DWORD):
            return False  # 无法解释的保护标志采用保守处理。
        address = _kernel32.GlobalLock(data)
        if not address:
            return False
        try:
            return bool(wintypes.DWORD.from_address(address).value)
        finally:
            _kernel32.GlobalUnlock(data)
    finally:
        _user32.CloseClipboard()


def startup_command() -> str:
    """使用绝对路径，Windows 登录时不依赖当前工作目录。"""
    if getattr(sys, "frozen", False):
        args = [sys.executable, "--start-hidden"]
    else:
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        executable = str(pythonw) if pythonw.exists() else sys.executable
        entry = Path(__file__).resolve().parents[2] / "run_clipnest.py"
        args = [executable, str(entry), "--start-hidden"]
    return subprocess.list2cmdline(args)


def set_startup(enabled: bool) -> None:
    if not IS_WINDOWS:
        raise OSError("开机自启仅支持 Windows")
    import winreg
    # 仅修改当前用户自己的 Run 项，无需管理员权限。
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, "ClipNest", 0, winreg.REG_SZ, startup_command())
        else:
            try:
                winreg.DeleteValue(key, "ClipNest")
            except FileNotFoundError:
                pass


def bring_to_front(widget) -> None:
    """还原并聚焦本软件窗口；不模拟按键，也不绕过系统权限。"""
    if widget.isMinimized():
        widget.showNormal()
    else:
        widget.show()
    widget.raise_()
    widget.activateWindow()
    if not IS_WINDOWS:
        return
    handle = int(widget.winId())
    if _user32.SetForegroundWindow(handle):
        return
    current = _kernel32.GetCurrentThreadId()
    foreground = _user32.GetWindowThreadProcessId(_user32.GetForegroundWindow(), None)
    attached = bool(foreground and foreground != current and _user32.AttachThreadInput(current, foreground, True))
    try:
        _user32.SetForegroundWindow(handle)
        widget.activateWindow()
    finally:
        if attached:
            _user32.AttachThreadInput(current, foreground, False)

