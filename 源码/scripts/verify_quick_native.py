"""用独立测试编辑器验证 Windows 前台焦点；不读写系统剪贴板。

列表点击与标题拖动使用 Windows SendInput，只操作测试自建窗口；
原生 WM_MOUSEACTIVATE 消息和跨进程前台窗口检查补充 Qt 控件测试。
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPoint, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QVBoxLayout, QWidget

from clipnest.core.repository import Repository
from clipnest.platform.settings import Settings
from clipnest.platform.windows import bring_to_front
from clipnest.ui.jobs import Jobs
from clipnest.ui.theme import apply_theme
from clipnest.ui.windows import QuickWindow


def atomic_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    try:
        temporary.replace(path)
    except PermissionError:
        # Windows 下另一个进程正读取时 replace 可能短暂被拒绝；下帧重试。
        pass


def helper(directory):
    app = QApplication([])
    window = QWidget()
    window.setWindowTitle("ClipNest 测试专用编辑器")
    window.resize(450, 120)
    window.move(40, 40)
    editor = QLineEdit("test-owned editor content")
    layout = QVBoxLayout(window)
    layout.addWidget(editor)
    window.show()
    bring_to_front(window)
    editor.setFocus()
    editor.setCursorPosition(7)
    status = directory / "editor.json"
    command = directory / "activate"
    configure = directory / "command.json"
    applied_command = 0

    def update():
        nonlocal applied_command
        if configure.exists():
            values = read_json(configure)
            if values:
                configure.unlink(missing_ok=True)
                if "text" in values:
                    editor.setText(values["text"])
                if "cursor" in values:
                    editor.setCursorPosition(values["cursor"])
                if "selection" in values:
                    editor.setSelection(*values["selection"])
                if values.get("activate"):
                    bring_to_front(window)
                    editor.setFocus()
                applied_command = values.get("serial", applied_command)
        if command.exists():
            command.unlink(missing_ok=True)
            bring_to_front(window)
            editor.setFocus()
        atomic_json(status, {
            "hwnd": int(window.winId()), "pid": os.getpid(),
            "ratio": window.devicePixelRatioF(),
            "cursor": editor.cursorPosition(), "selection": editor.selectedText(),
            "selection_start": editor.selectionStart(), "text": editor.text(),
            "focus": editor.hasFocus(), "serial": applied_command,
        })

    timer = QTimer(window)
    timer.timeout.connect(update)
    timer.start(50)
    update()
    app.exec()


def until(app, condition, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return
        time.sleep(0.01)
    assert condition(), "verification condition timed out"


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


class MouseInput(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class KeyboardInput(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class InputUnion(ctypes.Union):
    _fields_ = [("mi", MouseInput), ("ki", KeyboardInput)]


class Input(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("data", InputUnion)]


class NativeMouse:
    """仅在当前前台和目标点都属于测试窗口时发送鼠标输入。"""
    def __init__(self, user32, app, owned):
        self.api, self.app, self.owned = user32, app, owned
        self.down = False
        self.held_keys = set()
        self.api.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        self.api.GetCursorPos.restype = wintypes.BOOL
        self.api.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
        self.api.SetCursorPos.restype = wintypes.BOOL
        self.api.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self.api.GetAsyncKeyState.restype = ctypes.c_short
        self.api.GetSystemMetrics.argtypes = [ctypes.c_int]
        self.api.GetSystemMetrics.restype = ctypes.c_int
        self.api.WindowFromPoint.argtypes = [wintypes.POINT]
        self.api.WindowFromPoint.restype = wintypes.HWND
        self.api.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        self.api.GetAncestor.restype = wintypes.HWND
        self.api.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        self.api.ClientToScreen.restype = wintypes.BOOL
        self.api.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int]
        self.api.SendInput.restype = wintypes.UINT
        self.original = self.position()
        self.last = self.original

    def position(self):
        point = wintypes.POINT()
        assert self.api.GetCursorPos(ctypes.byref(point))
        return point.x, point.y

    def guard(self, check_idle=True):
        assert self.api.GetForegroundWindow() in self.owned, "user foreground changed; input aborted"
        current = self.position()
        assert abs(current[0] - self.last[0]) <= 2 and abs(current[1] - self.last[1]) <= 2, "user pointer moved; input aborted"
        if check_idle:
            held = [key for key in (1, 2, 4, 5, 6, 0x10, 0x11, 0x12, 0x5B, 0x5C)
                    if self.api.GetAsyncKeyState(key) & 0x8000]
            assert not held, "physical mouse button/modifier held; input aborted"

    def point_for(self, widget, local=None):
        window = widget.window()
        local = local or widget.rect().center()
        offset = widget.mapTo(window, local)
        ratio = window.devicePixelRatioF()
        point = wintypes.POINT(round(offset.x() * ratio), round(offset.y() * ratio))
        assert self.api.ClientToScreen(int(window.winId()), ctypes.byref(point))
        return point.x, point.y

    def send(self, target, flags=0):
        self.guard(check_idle=not self.down)
        if flags & 0x0002:  # 按下前确认光标目标是测试窗口，避免误点其他程序。
            at = self.api.WindowFromPoint(wintypes.POINT(*target))
            assert self.api.GetAncestor(at, 2) in self.owned, "pointer target is not test-owned"
        left = self.api.GetSystemMetrics(76)
        top = self.api.GetSystemMetrics(77)
        width = self.api.GetSystemMetrics(78)
        height = self.api.GetSystemMetrics(79)
        event = Input(0, InputUnion(MouseInput(
            round((target[0] - left) * 65535 / max(1, width - 1)),
            round((target[1] - top) * 65535 / max(1, height - 1)),
            0, 0x0001 | 0x8000 | 0x4000 | flags, 0, 0,
        )))
        assert self.api.SendInput(1, ctypes.byref(event), ctypes.sizeof(Input)) == 1, "SendInput failed"
        if flags & 0x0002:
            self.down = True
        if flags & 0x0004:
            self.down = False
        QTest.qWait(35)
        self.last = target
        actual = self.position()
        assert abs(actual[0] - target[0]) <= 2 and abs(actual[1] - target[1]) <= 2

    def click(self, target, twice=False):
        self.send(target)
        for _ in range(2 if twice else 1):
            self.send(target, 0x0002)
            self.send(target, 0x0004)

    def move_window(self, header, delta):
        start = self.point_for(header, QPoint(30, max(5, header.height() // 2)))
        ratio = header.window().devicePixelRatioF()
        physical_delta = (round(delta.x() * ratio), round(delta.y() * ratio))
        self.send(start)
        self.send(start, 0x0002)
        for step in range(1, 5):
            target = (start[0] + round(physical_delta[0] * step / 4),
                      start[1] + round(physical_delta[1] * step / 4))
            self.send(target)
        self.send(target, 0x0004)

    def press_key(self, virtual_key):
        self.guard()
        events = (Input * 2)(
            Input(1, InputUnion(ki=KeyboardInput(virtual_key, 0, 0, 0, 0))),
            Input(1, InputUnion(ki=KeyboardInput(virtual_key, 0, 0x0002, 0, 0))),
        )
        sent = self.api.SendInput(2, events, ctypes.sizeof(Input))
        if sent != 2:
            self.api.SendInput(1, ctypes.byref(events[1]), ctypes.sizeof(Input))
        assert sent == 2, "SendInput keyboard event failed"
        QTest.qWait(70)

    def key_down(self, virtual_key):
        self.guard()
        event = Input(1, InputUnion(ki=KeyboardInput(virtual_key, 0, 0, 0, 0)))
        assert self.api.SendInput(1, ctypes.byref(event), ctypes.sizeof(Input)) == 1
        self.held_keys.add(virtual_key)
        QTest.qWait(35)

    def key_up(self, virtual_key):
        event = Input(1, InputUnion(ki=KeyboardInput(virtual_key, 0, 0x0002, 0, 0)))
        assert self.api.SendInput(1, ctypes.byref(event), ctypes.sizeof(Input)) == 1
        self.held_keys.discard(virtual_key)
        QTest.qWait(35)

    def restore(self):
        for key in list(self.held_keys):
            event = Input(1, InputUnion(ki=KeyboardInput(key, 0, 0x0002, 0, 0)))
            self.api.SendInput(1, ctypes.byref(event), ctypes.sizeof(Input))
            self.held_keys.discard(key)
        if self.down:
            event = Input(0, InputUnion(MouseInput(0, 0, 0, 0x0004, 0, 0)))
            self.api.SendInput(1, ctypes.byref(event), ctypes.sizeof(Input))
            self.down = False
        # 用户在测试途中移动鼠标时保留用户的新位置。
        current = self.position()
        if abs(current[0] - self.last[0]) <= 2 and abs(current[1] - self.last[1]) <= 2:
            self.api.SetCursorPos(*self.original)


def verify(check_dropdowns=True):
    if sys.platform != "win32":
        raise RuntimeError("This verification requires Windows")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
    user32.SendMessageW.restype = ctypes.c_ssize_t
    app = QApplication.instance() or QApplication([])
    original_foreground = user32.GetForegroundWindow()
    results = []

    def record(message):
        results.append(message)
        print(message, flush=True)

    process = None
    quick = None
    jobs = None
    helper_handle = None
    quick_handle = None
    mouse = None
    try:
        with tempfile.TemporaryDirectory(prefix="ClipNest-native-test-") as temporary:
            directory = Path(temporary)
            # Windows venv python.exe 可能是启动器；用同版本的真实解释器，
            # 显式提供 venv 包目录，让 Popen PID 能对应到实际测试编辑器。
            helper_executable = getattr(sys, "_base_executable", sys.executable)
            helper_environment = os.environ.copy()
            package_directory = Path(sys.prefix) / "Lib" / "site-packages"
            helper_environment["PYTHONPATH"] = os.pathsep.join([
                str(package_directory), str(ROOT), helper_environment.get("PYTHONPATH", ""),
            ])
            process = subprocess.Popen(
                [helper_executable, str(Path(__file__).resolve()), "--helper", str(directory)],
                env=helper_environment,
            )
            status = directory / "editor.json"
            until(app, lambda: bool(read_json(status).get("hwnd")))
            helper_handle = read_json(status)["hwnd"]
            helper_pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(helper_handle, ctypes.byref(helper_pid))
            assert helper_pid.value == process.pid, "editor is not the test-owned process"
            until(app, lambda: user32.GetForegroundWindow() == helper_handle)
            before = read_json(status)
            mouse = NativeMouse(user32, app, {helper_handle})
            mouse.guard()
            repo = Repository(directory / "history.db", directory / "images")
            repo.add_text("test-owned history entry")
            settings = Settings(directory)
            jobs = Jobs()
            copied = []

            def copy(item, done=None):
                copied.append(item["id"])
                if done:
                    done()

            # 焦点回归不会读取/写入/粘贴用户的真实剪贴板。
            quick = QuickWindow(repo, jobs, copy, settings=settings,
                paste=lambda handle: (True, "已发送粘贴快捷键"))
            quick.summon()
            quick_handle = int(quick.winId())
            mouse.owned.add(quick_handle)
            until(app, lambda: quick.isVisible() and quick.pane.list.count() == 1)
            assert user32.GetForegroundWindow() == helper_handle
            QTest.qWait(150)
            assert read_json(status)["cursor"] == before["cursor"]
            assert read_json(status)["selection"] == before["selection"]
            assert read_json(status)["focus"]
            assert mouse.position() == mouse.original
            record("PASS: passive summon preserves separate editor foreground, caret, selection and physical pointer")

            # HTCLIENT + WM_LBUTTONDOWN；只询问窗口策略，不移动系统鼠标。
            response = user32.SendMessageW(quick_handle, 0x0021, helper_handle, 1 | (0x0201 << 16))
            assert response == 3, f"expected MA_NOACTIVATE, received {response}"
            assert user32.GetForegroundWindow() == helper_handle
            record("PASS: native WM_MOUSEACTIVATE returns MA_NOACTIVATE while passive")

            mouse.guard()

            dropdowns = ((quick.pane.kind, 1), (quick.pane.time, 2)) if check_dropdowns else ()
            for combo, selected_index in dropdowns:
                mouse.click(mouse.point_for(combo, QPoint(combo.width() - 14, combo.height() // 2)))
                popup = combo.view().window()
                until(app, popup.isVisible)
                mouse.owned.add(int(popup.winId()))
                assert user32.GetForegroundWindow() == helper_handle
                assert read_json(status)["cursor"] == before["cursor"]
                model_index = combo.view().model().index(selected_index, 0)
                item_center = combo.view().visualRect(model_index).center()
                mouse.click(mouse.point_for(combo.view().viewport(), item_center))
                until(app, lambda: combo.currentIndex() == selected_index and not popup.isVisible())
                assert user32.GetForegroundWindow() == helper_handle
            until(app, lambda: quick.pane.list.count() == 1 and not quick.pane.busy and not quick.pane.timer.isActive())
            if check_dropdowns:
                record("PASS: real type/time dropdown selection preserves separate editor foreground and caret")
            else:
                print("SKIP: type/time dropdown verification was explicitly disabled", flush=True)

            row = quick.pane.list.item(0)
            center = quick.pane.list.visualItemRect(row).center()
            mouse.click(mouse.point_for(quick.pane.list.viewport(), center))
            until(app, lambda: len(copied) == 1)
            assert quick.isVisible() and quick.passive
            assert user32.GetForegroundWindow() == helper_handle
            record("PASS: real mouse single click copies own record with fake paste, keeps popup open and preserves external editor foreground")

            origin = quick.pos()
            apply_theme(app, "dark")
            mouse.move_window(quick.brand_label, QPoint(31, 23))
            until(app, lambda: quick.pos() != origin)
            assert abs(quick.x() - origin.x() - 31) <= 1 and abs(quick.y() - origin.y() - 23) <= 1
            assert user32.GetForegroundWindow() == helper_handle
            assert read_json(status)["cursor"] == before["cursor"]
            position = [quick.x(), quick.y()]
            until(app, lambda: Settings(directory).get("quick_position") == position)
            quick.hide()
            quick.summon()
            until(app, quick.isVisible)
            assert [quick.x(), quick.y()] == position
            assert user32.GetForegroundWindow() == helper_handle
            record("PASS: real black-theme brand title drag changes/persists position and reopen preserves editor foreground")

            mouse.click(mouse.point_for(quick.pane.search))
            until(app, lambda: quick.pane.search.hasFocus() and user32.GetForegroundWindow() == quick_handle)
            assert not quick.passive
            record("PASS: explicitly clicking search activates the quick window")

            mouse.key_down(0x0D)
            QTest.qWait(80)
            assert len(copied) == 2
            assert user32.GetForegroundWindow() == quick_handle
            assert read_json(status)["cursor"] == before["cursor"]
            record("PASS: real held Enter keeps quick foreground until key release, avoiding Enter input in original editor")
            mouse.key_up(0x0D)
            until(app, lambda: len(copied) == 2 and user32.GetForegroundWindow() == helper_handle)
            assert quick.isVisible() and quick.passive
            assert read_json(status)["cursor"] == before["cursor"]
            record("PASS: real Enter after active search copies, returns foreground to editor and keeps popup visible")
            mouse.click(mouse.point_for(quick.pane.search))
            until(app, lambda: quick.pane.search.hasFocus() and user32.GetForegroundWindow() == quick_handle)

            # 真实点击测试编辑器的空白区域，避免改变文本选择和插入位置。
            helper_ratio = read_json(status)["ratio"]
            return_point = wintypes.POINT(round(25 * helper_ratio), round(105 * helper_ratio))
            assert user32.ClientToScreen(helper_handle, ctypes.byref(return_point))
            mouse.click((return_point.x, return_point.y))
            until(app, lambda: user32.GetForegroundWindow() == helper_handle)
            assert quick.isVisible()
            assert read_json(status)["cursor"] == before["cursor"]
            record("PASS: returning to separate editor leaves quick window visible")
            mouse.restore()
            if original_foreground:
                user32.SetForegroundWindow(original_foreground)
            quick.hide()
            jobs.close()
            jobs = None
            process.terminate()
            process.wait(timeout=5)
            process = None
    finally:
        if mouse is not None:
            mouse.restore()
        foreground = user32.GetForegroundWindow()
        if foreground in (helper_handle, quick_handle) and original_foreground:
            user32.SetForegroundWindow(original_foreground)
        if quick is not None:
            quick.hide()
        if jobs is not None:
            jobs.close()
        if process is not None:
            process.terminate()
            process.wait(timeout=5)
    print("PASS: SendInput mouse actions targeted test-owned windows only; original pointer/foreground restoration requested. No clipboard access.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--helper", type=Path)
    parser.add_argument("--skip-dropdowns", action="store_true")
    arguments = parser.parse_args()
    if arguments.helper:
        helper(arguments.helper)
    else:
        verify(check_dropdowns=not arguments.skip_dropdowns)
