"""后台任务统一回到 Qt 主线程，避免跨线程操作控件。"""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from PySide6.QtCore import QObject, Signal, Slot


class Jobs(QObject):
    finished = Signal(str, object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ClipNest")
        self.callbacks = {}
        self.finished.connect(self._complete)
        self.closed = False

    def submit(self, work, done, fail=None):
        if self.closed:
            return
        key = uuid4().hex
        self.callbacks[key] = (done, fail)
        future = self.pool.submit(work)

        def complete(result):
            try:
                value = result.result()
                error = ""
            except Exception as exc:
                value = None
                # 不把可能携带剪贴板原文的异常消息写入界面或日志。
                error = f"操作失败（{type(exc).__name__}），请检查数据目录权限、输入格式或可用空间。"
            self.finished.emit(key, value, error)
        future.add_done_callback(complete)

    @Slot(str, object, str)
    def _complete(self, key, value, error):
        callback = self.callbacks.pop(key, None)
        if callback and not self.closed:
            done, fail = callback
            if error:
                if fail:
                    fail(error)
            else:
                done(value)

    def close(self):
        self.closed = True
        self.callbacks.clear()
        self.pool.shutdown(wait=True, cancel_futures=True)
