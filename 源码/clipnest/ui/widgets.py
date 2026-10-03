"""快捷窗口的被动筛选控件：鼠标可操作，原应用仍接收键盘输入。"""
from PySide6.QtCore import Qt, QTimer, QPoint
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication, QComboBox, QListWidget, QListWidgetItem, QVBoxLayout, QWidget
from clipnest.platform import windows as win_api


class _PassivePicker(QWidget):
    def __init__(self, combo):
        super().__init__(combo.window(), Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.combo = combo
        self.setObjectName("quickCard")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list = QListWidget(self)
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.addWidget(self.list)
        self.list.itemClicked.connect(self.choose)
        self.outside = QTimer(self)
        self.outside.setInterval(50)
        self.outside.timeout.connect(self.check_outside)

    def choose(self, item):
        self.combo.setCurrentIndex(item.data(Qt.ItemDataRole.UserRole))
        self.hide()

    def show_options(self):
        self.list.clear()
        for index in range(self.combo.count()):
            item = QListWidgetItem(self.combo.itemIcon(index), self.combo.itemText(index))
            item.setData(Qt.ItemDataRole.UserRole, index)
            self.list.addItem(item)
        self.list.setCurrentRow(self.combo.currentIndex())
        screen = self.combo.screen()
        area = screen.availableGeometry()
        row_height = max(28, self.list.sizeHintForRow(0))
        self.resize(min(max(self.combo.width(), 140), area.width()), min(row_height * min(self.combo.count(), 10) + 8, area.height()))
        origin = self.combo.mapToGlobal(QPoint(0, self.combo.height()))
        if origin.y() + self.height() > area.bottom() + 1:
            origin.setY(self.combo.mapToGlobal(QPoint(0, 0)).y() - self.height())
        origin.setX(max(area.left(), min(origin.x(), area.right() - self.width() + 1)))
        origin.setY(max(area.top(), min(origin.y(), area.bottom() - self.height() + 1)))
        self.move(origin)
        win_api.show_no_activate_topmost(self)
        self.outside.start()

    def check_outside(self):
        # 只观察按键状态，不安装鼠标钩子、不吞掉原应用的点击。
        down = win_api.left_button_down() if win_api.IS_WINDOWS else bool(QApplication.mouseButtons() & Qt.MouseButton.LeftButton)
        if not down:
            return
        point = QCursor.pos()
        combo_rect = self.combo.rect().translated(self.combo.mapToGlobal(QPoint(0, 0)))
        if not self.frameGeometry().contains(point) and not combo_rect.contains(point):
            self.hide()

    def hideEvent(self, event):
        self.outside.stop()
        super().hideEvent(event)

    def nativeEvent(self, event_type, message):
        if win_api.IS_WINDOWS and bytes(event_type) in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            from ctypes import wintypes
            if wintypes.MSG.from_address(int(message)).message == 0x0021:
                return True, 3  # MA_NOACTIVATE，选择选项时不切换前台窗口。
        return super().nativeEvent(event_type, message)


class PassiveComboBox(QComboBox):
    """被动模式避开 Qt.Popup 的强制激活；主动搜索仍使用标准下拉框。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._picker = None

    def _passive(self):
        return getattr(self.window(), "passive", False)

    def passive_view(self):
        if self._picker is None:
            self._picker = _PassivePicker(self)
        return self._picker.list

    def view(self):
        return self.passive_view() if self._passive() else super().view()

    def showPopup(self):
        if self._passive():
            self.passive_view()
            if self._picker.isVisible():
                self._picker.hide()
            else:
                self._picker.show_options()
        else:
            super().showPopup()

    def hidePopup(self):
        if self._picker is not None:
            self._picker.hide()
        super().hidePopup()
