"""已禁用的原生粘贴诊断脚本，仅保留用于定位测试恢复故障。

2026-10-03 的真实粘贴行为通过，但最后 OLE 恢复路径在 ole32.dll
中以 0xc000041d 崩溃，恢复未获确认。因此禁止再次执行该路径。
以下源码仅作诊断，不能当作原剪贴板可靠恢复方案。
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from clipnest.core.repository import Repository
from clipnest.platform.clipboard import ClipboardMonitor
from clipnest.platform.settings import Settings
from clipnest.ui.jobs import Jobs
from clipnest.ui.theme import apply_theme
from clipnest.ui.windows import QuickWindow
from verify_quick_native import NativeMouse, atomic_json, read_json, until


class ClipboardPreserver:
    """不转换成纯文本；保留 OLE 对象，恢复后固化其完整可用格式。"""
    def __init__(self, user32):
        self.api = user32
        self.ole = ctypes.OleDLL("ole32")
        self.pointer = ctypes.c_void_p()
        self.initialized = False
        self.api.OpenClipboard.argtypes = [wintypes.HWND]
        self.api.OpenClipboard.restype = wintypes.BOOL
        self.api.CloseClipboard.argtypes = []
        self.api.CloseClipboard.restype = wintypes.BOOL
        self.api.EnumClipboardFormats.argtypes = [wintypes.UINT]
        self.api.EnumClipboardFormats.restype = wintypes.UINT
        self.api.GetClipboardData.argtypes = [wintypes.UINT]
        self.api.GetClipboardData.restype = wintypes.HANDLE
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.GlobalSize.argtypes = [wintypes.HGLOBAL]
        self.kernel.GlobalSize.restype = ctypes.c_size_t
        self.kernel.GlobalLock.argtypes = [wintypes.HGLOBAL]
        self.kernel.GlobalLock.restype = ctypes.c_void_p
        self.kernel.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        self.kernel.GlobalUnlock.restype = wintypes.BOOL
        self.ole.OleInitialize.argtypes = [ctypes.c_void_p]
        self.ole.OleInitialize.restype = ctypes.c_long
        self.ole.OleUninitialize.argtypes = []
        self.ole.OleUninitialize.restype = None
        self.ole.OleGetClipboard.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
        self.ole.OleGetClipboard.restype = ctypes.c_long
        self.ole.OleSetClipboard.argtypes = [ctypes.c_void_p]
        self.ole.OleSetClipboard.restype = ctypes.c_long
        self.ole.OleIsCurrentClipboard.argtypes = [ctypes.c_void_p]
        self.ole.OleIsCurrentClipboard.restype = ctypes.c_long
        self.ole.OleFlushClipboard.argtypes = []
        self.ole.OleFlushClipboard.restype = ctypes.c_long

    def formats(self):
        self.open()
        result = set()
        try:
            current = 0
            while True:
                current = self.api.EnumClipboardFormats(current)
                if not current:
                    return result
                result.add(current)
        finally:
            self.api.CloseClipboard()

    def __enter__(self):
        initialized = self.ole.OleInitialize(None)
        if initialized not in (0, 1):
            raise RuntimeError("OLE initialization failed; no clipboard write performed")
        self.initialized = True
        try:
            self.retry(lambda: self.ole.OleGetClipboard(ctypes.byref(self.pointer)),
                "original OLE clipboard unavailable; no clipboard write performed")
            if not self.pointer.value:
                raise RuntimeError("original OLE clipboard unavailable; no clipboard write performed")
            self.original_formats = self.formats()
            self.original_signatures = self.signatures(self.original_formats)
        except Exception:
            self.release()
            raise
        return self

    def retry(self, operation, label):
        # OLE 可能因另一个程序短暂锁定而失败；只做有上限的恢复/读取重试。
        for attempt in range(10):
            try:
                if operation() == 0:
                    return
            except OSError:
                pass
            QTest.qWait(40)
        raise RuntimeError(label)

    def open(self):
        for attempt in range(10):
            if self.api.OpenClipboard(None):
                return
            QTest.qWait(40)
        raise RuntimeError("clipboard busy; cannot verify original formats/data")

    def release(self):
        if self.pointer.value:
            table = ctypes.cast(self.pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            release = ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)(table[2])
            release(self.pointer)
            self.pointer = ctypes.c_void_p()
        if self.initialized:
            self.ole.OleUninitialize()
            self.initialized = False

    def signatures(self, formats):
        # 仅对明确使用 HGLOBAL 的常见格式做本地哈希；原内容不输出、不落盘。
        self.open()
        result = {}
        try:
            for format_id in set(formats) & {1, 7, 8, 13, 15, 17}:
                handle = self.api.GetClipboardData(format_id)
                size = self.kernel.GlobalSize(handle) if handle else 0
                if not size or size > 128 * 1024 * 1024:
                    continue
                address = self.kernel.GlobalLock(handle)
                if not address:
                    raise RuntimeError("cannot preserve clipboard data signature")
                try:
                    data = ctypes.string_at(address, size)
                finally:
                    self.kernel.GlobalUnlock(handle)
                if format_id == 13:
                    for offset in range(0, len(data) - 1, 2):
                        if data[offset:offset + 2] == b"\0\0":
                            data = data[:offset]
                            break
                elif format_id in (1, 7):
                    data = data.split(b"\0", 1)[0]
                result[format_id] = hashlib.sha256(data).digest()
            return result
        finally:
            self.api.CloseClipboard()

    def __exit__(self, exception_type, exception, traceback):
        try:
            self.retry(lambda: self.ole.OleSetClipboard(self.pointer),
                "original OLE clipboard restoration failed")
            if self.ole.OleIsCurrentClipboard(self.pointer) != 0:
                raise RuntimeError("original OLE object is not current clipboard owner")
            self.retry(self.ole.OleFlushClipboard, "original clipboard format flush failed")
            restored = self.formats()
            if not self.original_formats.issubset(restored):
                raise RuntimeError("some original clipboard formats were not restored")
            if self.signatures(self.original_signatures) != self.original_signatures:
                raise RuntimeError("original clipboard data signatures do not match after restoration")
            print("PASS: original OLE clipboard restored; original formats and common data signatures verified", flush=True)
        finally:
            self.release()


def verify():
    raise RuntimeError("real clipboard verification disabled: OLE restoration crashed; preserve current clipboard")
    if sys.platform != "win32":
        raise RuntimeError("Windows is required")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    app = QApplication.instance() or QApplication([])
    original_foreground = user32.GetForegroundWindow()
    process = None
    jobs = None
    quick = None
    mouse = None
    monitor = None
    helper_handle = None
    quick_handle = None
    with ClipboardPreserver(user32):
        try:
            with tempfile.TemporaryDirectory(prefix="ClipNest-paste-test-") as temporary:
                directory = Path(temporary)
                environment = os.environ.copy()
                environment["PYTHONPATH"] = os.pathsep.join([
                    str(Path(sys.prefix) / "Lib" / "site-packages"), str(ROOT),
                    environment.get("PYTHONPATH", ""),
                ])
                process = subprocess.Popen([
                    getattr(sys, "_base_executable", sys.executable),
                    str(Path(__file__).with_name("verify_quick_native.py")), "--helper", str(directory),
                ], env=environment)
                status = directory / "editor.json"
                until(app, lambda: bool(read_json(status).get("hwnd")))
                helper_handle = read_json(status)["hwnd"]
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(helper_handle, ctypes.byref(pid))
                assert pid.value == process.pid == read_json(status)["pid"]
                until(app, lambda: user32.GetForegroundWindow() == helper_handle)
                mouse = NativeMouse(user32, app, {helper_handle})
                mouse.guard()
                repo = Repository(directory / "history.db", directory / "images")
                first = repo.add_text("CLIPNEST_NATIVE_PASTE_1")
                second = repo.add_text("CLIPNEST_NATIVE_PASTE_2")
                settings = Settings(directory)
                settings.set("paused", True)
                settings.set("close_after_paste", False)
                jobs = Jobs()
                monitor = ClipboardMonitor(settings)
                copies = []

                def copy(item, done=None):
                    assert monitor.copy_text(item["text"]), "test clipboard write failed"
                    copies.append(item["id"])
                    if done:
                        done()

                quick = QuickWindow(repo, jobs, copy, settings=settings)
                quick_handle = int(quick.winId())
                mouse.owned.add(quick_handle)
                serial = 0

                def configure(text, cursor=None, selection=None):
                    nonlocal serial
                    serial += 1
                    values = {"serial": serial, "text": text, "activate": True}
                    if cursor is not None:
                        values["cursor"] = cursor
                    if selection is not None:
                        values["selection"] = selection
                    atomic_json(directory / "command.json", values)
                    until(app, lambda: read_json(status).get("serial") == serial
                        and user32.GetForegroundWindow() == helper_handle)

                def summon_and_find(record):
                    quick.summon()
                    until(app, lambda: quick.isVisible() and not quick.pane.busy and not quick.pane.timer.isActive())
                    for index in range(quick.pane.list.count()):
                        item = quick.pane.list.item(index)
                        if item.data(0x0100)["id"] == record["id"]:
                            return item
                    raise AssertionError("test record missing from quick list")

                def click_body(item):
                    rectangle = quick.pane.list.visualItemRect(item)
                    point = QPoint(rectangle.left() + 110, rectangle.center().y())
                    mouse.click(mouse.point_for(quick.pane.list.viewport(), point))

                configure("ABCD", cursor=2)
                item = summon_and_find(first)
                click_body(item)
                expected = "AB" + first["text"] + "CD"
                until(app, lambda: read_json(status).get("text") == expected)
                assert read_json(status)["cursor"] == 2 + len(first["text"])
                assert len(copies) == 1 and quick.isVisible()
                assert user32.GetForegroundWindow() == helper_handle
                print("PASS: real single click pastes exactly once at original editor caret and keeps quick visible", flush=True)

                configure("alpha OLD omega", selection=[6, 3])
                item = summon_and_find(second)
                mouse.click(mouse.point_for(quick.pane.search))
                until(app, lambda: quick.pane.search.hasFocus() and user32.GetForegroundWindow() == quick_handle)
                quick.pane.search.setText("PASTE_2")
                until(app, lambda: quick.pane.list.count() == 1 and not quick.pane.busy and not quick.pane.timer.isActive())
                click_body(quick.pane.list.item(0))
                until(app, lambda: read_json(status).get("text") == "alpha " + second["text"] + " omega")
                assert read_json(status)["selection"] == ""
                assert len(copies) == 2 and quick.isVisible()
                assert user32.GetForegroundWindow() == helper_handle
                print("PASS: after active search, single click restores editor and replaces original selected text", flush=True)

                configure("dark drag test", cursor=4)
                settings.set("theme", "dark")
                apply_theme(app, "dark")
                quick.pane.search.clear()
                quick.summon()
                until(app, lambda: quick.isVisible() and not quick.pane.busy and not quick.pane.timer.isActive())
                origin = quick.pos()
                mouse.move_window(quick.brand_label, QPoint(29, 21))
                until(app, lambda: quick.pos() != origin)
                assert abs(quick.x() - origin.x() - 29) <= 1 and abs(quick.y() - origin.y() - 21) <= 1
                assert read_json(status)["cursor"] == 4 and len(copies) == 2
                assert user32.GetForegroundWindow() == helper_handle
                print("PASS: real title drag works in black theme, preserving external caret and avoiding paste", flush=True)

                mouse.click(mouse.point_for(quick.pane.bulk_button))
                assert quick.pane.list.is_bulk_mode()
                until(app, lambda: quick.pane.list.count() == 2)
                click_body(quick.pane.list.item(0))
                click_body(quick.pane.list.item(1))
                assert len(quick.pane.list.selected_records()) == 2 and len(copies) == 2
                assert read_json(status)["text"] == "dark drag test"
                mouse.click(mouse.point_for(quick.pane.bulk_button))
                assert not quick.pane.list.is_bulk_mode()
                deleted = []
                quick.pane.list.delete_requested.connect(lambda record: deleted.append(record["id"]))
                rectangle = quick.pane.list.delete_button_rect(quick.pane.list.item(0))
                mouse.click(mouse.point_for(quick.pane.list.viewport(), rectangle.center()))
                until(app, lambda: len(deleted) == 1)
                until(app, lambda: repo.get(deleted[0]) is None and quick.pane.list.count() == 1 and not quick.pane.busy)
                assert len(copies) == 2 and read_json(status)["text"] == "dark drag test"
                print("PASS: real batch selection and right-side delete request do not paste into external editor", flush=True)

                configure("XY", cursor=1)
                settings.set("close_after_paste", True)
                item = summon_and_find(first)
                click_body(item)
                until(app, lambda: read_json(status).get("text") == "X" + first["text"] + "Y" and not quick.isVisible())
                assert len(copies) == 3 and user32.GetForegroundWindow() == helper_handle
                print("PASS: enabling close-after-paste hides quick window only after successful real paste", flush=True)

                quick.hide()
                monitor.stop()
                jobs.close()
                jobs = None
                mouse.restore()
                user32.SetForegroundWindow(original_foreground)
                process.terminate()
                process.wait(timeout=5)
                process = None
        finally:
            if mouse is not None:
                mouse.restore()
            if user32.GetForegroundWindow() in (helper_handle, quick_handle) and original_foreground:
                user32.SetForegroundWindow(original_foreground)
            if quick is not None:
                quick.hide()
            if monitor is not None:
                monitor.stop()
            if jobs is not None:
                jobs.close()
            if process is not None:
                process.terminate()
                process.wait(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-clipboard-test", action="store_true")
    arguments = parser.parse_args()
    parser.error("disabled after OLE restoration crash; no clipboard access performed")
