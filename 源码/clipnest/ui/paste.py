"""异步粘贴准备：等待修饰键松开，焦点变化时取消，绝不阻塞界面。"""
from time import monotonic
from PySide6.QtCore import QObject, QTimer
from clipnest.platform import windows as win_api


class PasteController(QObject):
    def __init__(self, parent, send=None):
        super().__init__(parent)
        self.send = send or win_api.paste_to_window
        self.timer = QTimer(self)
        self.timer.setInterval(20)
        self.timer.timeout.connect(self._tick)
        self.callback = None

    def start(self, target, owned_handles, done):
        self.cancel()
        self.target, self.owned = target, set(owned_handles)
        self.restore_attempted = False
        self.callback = done
        if not win_api.window_is_valid(target) or target in self.owned:
            self._finish(False, "没有可粘贴的原输入框；内容已复制，可切到目标软件后按 Ctrl+V")
            return
        self.deadline = monotonic() + 1.2
        self.timer.start()
        self._tick()

    def _tick(self):
        if not self.callback:
            return
        foreground = win_api.foreground_window()
        if not win_api.window_is_valid(self.target) or foreground not in self.owned | {self.target}:
            self._finish(False, "输入焦点已切换，本次未粘贴；内容已复制，可手动按 Ctrl+V")
        elif not win_api.paste_gesture_down() and foreground in self.owned and not self.restore_attempted:
            # Enter 松开后才切回原输入框，长按 Enter 不会在目标软件重复回车。
            self.restore_attempted = True
            if not win_api.restore_foreground(self.target):
                self._finish(False, "无法返回原输入框；内容已复制，可手动按 Ctrl+V")
        elif foreground == self.target and not win_api.paste_gesture_down():
            success, message = self.send(self.target)
            self._finish(success, message)
        elif monotonic() >= self.deadline:
            message = "请松开 Enter、Ctrl、Alt、Shift 或 Win 键再选择记录；内容已复制" if win_api.paste_gesture_down() else "原输入框未恢复焦点；内容已复制，可手动按 Ctrl+V"
            self._finish(False, message)

    def _finish(self, success, message):
        self.timer.stop()
        callback, self.callback = self.callback, None
        if callback:
            callback(success, message)

    def cancel(self):
        self.timer.stop()
        self.callback = None
