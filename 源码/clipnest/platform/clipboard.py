"""Qt 主线程只取快照；保存、缩略图与搜索由业务层后台完成。"""
from __future__ import annotations

import uuid

from PySide6.QtCore import QMimeData, QObject, QTimer, Signal
from PySide6.QtGui import QClipboard, QGuiApplication, QImage, QPixmap, QTextDocument

from .settings import Settings
from . import windows

MAX_TEXT_CHARACTERS = 2_000_000
MAX_IMAGE_PIXELS = 25_000_000
OWN_MIME = "application/x-clipnest-owned"


class ClipboardMonitor(QObject):
    captured_text = Signal(str, str)  # 原始内容、来源可执行文件名
    captured_image = Signal(QImage, str)
    error = Signal(str)

    def __init__(self, settings: Settings, parent: QObject | None = None):
        super().__init__(parent)
        app = QGuiApplication.instance()
        if app is None:
            raise RuntimeError("先创建 QApplication，再创建 ClipboardMonitor")
        self.settings = settings
        self.paused = bool(settings.get("paused", False))
        self._clipboard = QGuiApplication.clipboard()
        self._running = False
        self._writing = False
        self._own_token = uuid.uuid4().hex.encode("ascii")
        self._own_sequence = -1
        self._last_sequence = -1
        self._pending_sequence = -1
        self._pending_source = ""
        self._retry_count = 0
        self._retry = QTimer(self)
        self._retry.setSingleShot(True)
        self._retry.setInterval(60)
        self._retry.timeout.connect(self._capture)

    def start(self) -> None:
        if self._running:
            return
        # 启动时不读取已有剪贴板，避免意外记录用户之前的敏感内容。
        self._last_sequence = windows.clipboard_sequence()
        self._clipboard.dataChanged.connect(self._on_changed)
        self._running = True

    def stop(self) -> None:
        self._retry.stop()
        if self._running:
            self._clipboard.dataChanged.disconnect(self._on_changed)
        self._running = False

    def set_paused(self, paused: bool) -> None:
        self.paused = bool(paused)
        self.settings.set("paused", self.paused)
        self._retry.stop()
        self._last_sequence = windows.clipboard_sequence()

    def _on_changed(self) -> None:
        if not self._running or self.paused or self._writing:
            return
        self._retry.stop()
        sequence = windows.clipboard_sequence()
        if windows.IS_WINDOWS and sequence in (self._last_sequence, self._own_sequence):
            return
        excluded = set(self.settings.get("excluded_apps", []))
        # 同时核查拥有剪贴板的窗口和当前前台，降低焦点切换期间漏排除的概率。
        owner = windows.clipboard_owner_process()
        foreground = windows.foreground_process()
        if owner.casefold() in excluded or foreground.casefold() in excluded:
            self._last_sequence = sequence
            return
        self._pending_source = owner or foreground
        self._pending_sequence = sequence
        self._retry_count = 0
        self._capture()

    def _later(self) -> None:
        self._retry_count += 1
        if self._retry_count <= 5:
            self._retry.start()
        else:
            self._last_sequence = self._pending_sequence
            self.error.emit("剪贴板暂时被其他应用占用，本次内容未记录。")

    def _capture(self) -> None:
        if not self._running or self.paused or self._writing:
            return
        sequence = windows.clipboard_sequence()
        if windows.IS_WINDOWS and sequence != self._pending_sequence:
            self._on_changed()
            return
        permission = windows.can_capture_sensitive_flags()
        if permission is None:
            self._later()
            return
        if not permission:
            self._last_sequence = sequence
            return
        try:
            mime = self._clipboard.mimeData(QClipboard.Mode.Clipboard)
            if mime is None:
                self._later()
                return
            self._last_sequence = sequence
            if mime.hasFormat(OWN_MIME) and bytes(mime.data(OWN_MIME)) == self._own_token:
                return
            # 文件复制不作为文本历史保存，也不访问所复制文件的内容。
            if mime.hasUrls() and all(url.isLocalFile() for url in mime.urls()):
                return
            if mime.hasImage():
                raw = mime.imageData()
                image = raw.toImage() if isinstance(raw, QPixmap) else raw
                if not isinstance(image, QImage) or image.isNull():
                    return
                if image.width() * image.height() > MAX_IMAGE_PIXELS:
                    self.error.emit("图片超过 2500 万像素，本次内容未记录。")
                    return
                snapshot = image.copy()
                if snapshot.isNull():
                    self.error.emit("无法创建图片快照，本次内容未记录。")
                    return
                if not self._still_current(sequence):
                    return
                self.captured_image.emit(snapshot, self._pending_source)
                return
            if mime.hasText():
                text = mime.text()
            elif mime.hasHtml():
                # 富文本只保存纯文本；不加载外部图片或其他资源。
                html = mime.html()
                if len(html) > MAX_TEXT_CHARACTERS:
                    self.error.emit("富文本超过 200 万字符，本次内容未记录。")
                    return
                document = QTextDocument()
                document.setHtml(html)
                text = document.toPlainText()
            elif mime.hasUrls():
                text = "\n".join(url.toString() for url in mime.urls())
            else:
                return
            if not text:
                return
            if len(text) > MAX_TEXT_CHARACTERS:
                self.error.emit("文本超过 200 万字符，本次内容未记录。")
                return
            if not self._still_current(sequence):
                return
            # 不 strip：代码的缩进、末尾空格与换行均保持原样。
            self.captured_text.emit(text, self._pending_source)
        except (RuntimeError, MemoryError):
            self.error.emit("无法读取本次剪贴板内容，请重新复制后再试。")

    def _still_current(self, sequence: int) -> bool:
        # 延迟渲染可能在读取过程中改变系统 sequence；舍弃旧来源对应的新内容。
        if windows.IS_WINDOWS and windows.clipboard_sequence() != sequence:
            self._on_changed()
            return False
        excluded = set(self.settings.get("excluded_apps", []))
        return (windows.clipboard_owner_process().casefold() not in excluded
                and windows.foreground_process().casefold() not in excluded)

    def _copy(self, mime: QMimeData) -> bool:
        self._retry.stop()
        mime.setData(OWN_MIME, self._own_token)
        self._writing = True
        try:
            self._clipboard.setMimeData(mime, QClipboard.Mode.Clipboard)
            self._own_sequence = windows.clipboard_sequence()
            self._last_sequence = self._own_sequence
            current = self._clipboard.mimeData(QClipboard.Mode.Clipboard)
            if current is None or bytes(current.data(OWN_MIME)) != self._own_token:
                self.error.emit("复制失败：剪贴板当前不可写，请稍后重试。")
                return False
            return True
        except RuntimeError:
            self.error.emit("复制失败：剪贴板当前不可写，请稍后重试。")
            return False
        finally:
            self._writing = False

    def copy_text(self, text: str) -> bool:
        mime = QMimeData()
        mime.setText(text)
        return self._copy(mime)

    def copy_image(self, image: QImage) -> bool:
        if image.isNull():
            self.error.emit("图片无法读取，复制失败。")
            return False
        mime = QMimeData()
        mime.setImageData(image)
        return self._copy(mime)
