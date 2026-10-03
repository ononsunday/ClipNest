from datetime import datetime
from PySide6.QtCore import Qt, QSize, QRect, QRectF, QModelIndex, QPersistentModelIndex, Signal
from PySide6.QtGui import QColor, QPainter, QFont, QPen
from PySide6.QtWidgets import QApplication, QListWidget, QListWidgetItem, QStyledItemDelegate, QStyle

KINDS = {"text": "文本", "code": "代码", "url": "链接", "email": "邮箱", "image": "图片", "color": "颜色"}


def _delete_rect(rect):
    """删除按钮的绘制与点击检测共用同一组视口坐标。"""
    return QRect(rect.right() - 43, rect.top() + (rect.height() - 28) // 2, 28, 28)


class HistoryList(QListWidget):
    delete_requested = Signal(object)
    activate_requested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setItemDelegate(HistoryDelegate(self))
        self.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setProperty("clipnestBulk", False)
        self.setProperty("clipnestDeleteHoverRow", -1)
        self._delete_index = None
        self._delete_gesture = False
        self._body_index = None
        self._body_origin = None
        self._body_moved = False
        self._suppress_next_release = False

    def is_bulk_mode(self):
        return bool(self.property("clipnestBulk"))

    def set_bulk_mode(self, enabled):
        enabled = bool(enabled)
        self.setProperty("clipnestBulk", enabled)
        self.setSelectionMode(QListWidget.SelectionMode.MultiSelection if enabled else QListWidget.SelectionMode.SingleSelection)
        self.clearSelection()
        self._delete_index = self._body_index = None
        self._delete_gesture = False
        self.viewport().update()

    def selected_records(self):
        return [item.data(Qt.ItemDataRole.UserRole) for item in sorted(self.selectedItems(), key=self.row)
                if item.data(Qt.ItemDataRole.UserRole) is not None]

    def delete_button_rect(self, item_or_index):
        index = self.indexFromItem(item_or_index) if isinstance(item_or_index, QListWidgetItem) else QModelIndex(item_or_index)
        return _delete_rect(self.visualRect(index)) if index.isValid() else QRect()

    def _delete_at(self, point):
        index = self.indexAt(point)
        return index if index.isValid() and self.delete_button_rect(index).contains(point) else QModelIndex()

    def mousePressEvent(self, event):
        point = event.position().toPoint()
        index = self._delete_at(point)
        self._body_index = self._body_origin = None
        self._body_moved = False
        if index.isValid():
            self._delete_gesture = True
            self._delete_index = QPersistentModelIndex(index) if event.button() == Qt.MouseButton.LeftButton else None
            event.accept()
            return
        self._delete_gesture = False
        self._delete_index = None
        if event.button() == Qt.MouseButton.LeftButton:
            index = self.indexAt(point)
            if index.isValid():
                self._body_index = QPersistentModelIndex(index)
                self._body_origin = point
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        point = event.position().toPoint()
        if self._suppress_next_release:
            self._suppress_next_release = False
            self._delete_index = self._body_index = None
            self._delete_gesture = False
            event.accept()
            return
        if self._delete_gesture:
            pressed = self._delete_index
            self._delete_index = None
            self._delete_gesture = False
            index = self._delete_at(point)
            if event.button() == Qt.MouseButton.LeftButton and pressed is not None and pressed.isValid() and index == pressed:
                self.delete_requested.emit(pressed.data(Qt.ItemDataRole.UserRole))
            event.accept()
            return
        if self._delete_at(point).isValid():
            self._body_index = None
            event.accept()
            return
        pressed = self._body_index
        index = self.indexAt(point)
        record = (pressed.data(Qt.ItemDataRole.UserRole) if pressed is not None and pressed.isValid()
                  and index == pressed and not self._body_moved else None)
        self._body_index = self._body_origin = None
        super().mouseReleaseEvent(event)
        # 业务只接收明确的左键单击；右键菜单和批量点选不能触发粘贴。
        if event.button() == Qt.MouseButton.LeftButton and bool(self.property("singleAction")) and not self.is_bulk_mode() and record is not None:
            self.activate_requested.emit(record)

    def mouseDoubleClickEvent(self, event):
        if self._delete_at(event.position().toPoint()).isValid() or self.is_bulk_mode() or bool(self.property("singleAction")):
            self._suppress_next_release = True
            self._delete_index = self._body_index = None
            self._delete_gesture = False
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event):
        point = event.position().toPoint()
        if self._body_origin is not None and (point - self._body_origin).manhattanLength() >= QApplication.startDragDistance():
            self._body_moved = True
        index = self._delete_at(point)
        hovered = index.row() if index.isValid() else -1
        if hovered != self.property("clipnestDeleteHoverRow"):
            self.setProperty("clipnestDeleteHoverRow", hovered)
            self.viewport().update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.setProperty("clipnestDeleteHoverRow", -1)
        self.viewport().update()
        super().leaveEvent(event)


class HistoryDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(260, 100)

    def paint(self, painter, option, index):
        record = index.data(Qt.ItemDataRole.UserRole) or {}
        dark = bool(QApplication.instance().property("clipnestDark"))
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        view = self.parent()
        bulk = bool(view.property("clipnestBulk")) if view else False
        accent = QColor("#6f99c1" if dark else "#4c9af0")
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(option.rect).adjusted(3, 3, -3, -3)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(("#13243a" if dark else "#e9f3ff") if selected else ("#141414" if dark else "#ffffff")))
        painter.drawRoundedRect(rect, 8, 8)
        delete_rect = _delete_rect(option.rect)
        left = rect.left() + (41 if bulk else 13)
        right = delete_rect.left() - 10
        text_width = max(0, int(right - left))
        if bulk:
            checkbox = QRectF(rect.left() + 13, rect.center().y() - 8, 16, 16)
            painter.setPen(QPen(QColor("#356893" if selected and dark else "#2e86e4" if selected else "#77818d"), 1))
            painter.setBrush(QColor("#245b90" if dark else "#2e86e4") if selected else QColor("#141414" if dark else "#ffffff"))
            painter.drawRoundedRect(checkbox, 4, 4)
            if selected:
                painter.setPen(QPen(QColor("#ffffff"), 1.6))
                painter.drawLine(checkbox.left() + 4, checkbox.top() + 8, checkbox.left() + 7, checkbox.top() + 11)
                painter.drawLine(checkbox.left() + 7, checkbox.top() + 11, checkbox.left() + 12, checkbox.top() + 5)
        hovered = view and view.property("clipnestDeleteHoverRow") == index.row()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#382125" if dark else "#fff0f1") if hovered else Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(delete_rect), 6, 6)
        painter.setPen(QPen(QColor("#e6858d" if hovered else "#9b7279" if dark else "#aa6570"), 1.4))
        cx, cy = delete_rect.center().x(), delete_rect.center().y()
        painter.drawLine(cx - 7, cy - 6, cx + 7, cy - 6)
        painter.drawLine(cx - 3, cy - 8, cx + 3, cy - 8)
        painter.drawRoundedRect(QRectF(cx - 5, cy - 4, 10, 12), 1, 1)
        painter.drawLine(cx - 2, cy - 1, cx - 2, cy + 5)
        painter.drawLine(cx + 2, cy - 1, cx + 2, cy + 5)
        kind = KINDS.get(record.get("kind"), record.get("kind", "文本"))
        indicators = ("  ★" if record.get("favorite") else "") + ("  ↑ 置顶" if record.get("pinned") else "")
        painter.setPen(accent)
        font = QFont(option.font)
        font.setPointSize(9)
        painter.setFont(font)
        label = painter.fontMetrics().elidedText(kind + indicators, Qt.TextElideMode.ElideRight, text_width)
        painter.drawText(QRectF(left, rect.top() + 10, text_width, 22), Qt.AlignmentFlag.AlignTop, label)
        painter.setPen(QColor("#eeeeee" if dark else "#23344c"))
        font.setPointSize(10)
        painter.setFont(font)
        text = record.get("title") or record.get("text") or f"图片 {record.get('width', 0)} × {record.get('height', 0)}"
        text = " ".join(text[:300].split())
        text = painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, text_width)
        painter.drawText(QRectF(left, rect.top() + 33, text_width, 25), Qt.AlignmentFlag.AlignTop, text)
        painter.setPen(QColor("#999fa9" if dark else "#7e8fa3"))
        font.setPointSize(9)
        painter.setFont(font)
        stamp = record.get("last_copied_at", "")
        try:
            stamp = datetime.fromisoformat(stamp).astimezone().strftime("%m-%d  %H:%M")
        except ValueError:
            stamp = stamp[:16]
        suffix = " · " + ", ".join(record.get("tags", [])[:2]) if record.get("tags") else ""
        foot = painter.fontMetrics().elidedText(stamp + suffix, Qt.TextElideMode.ElideRight, text_width)
        painter.drawText(QRectF(left, rect.top() + 64, text_width, 22), Qt.AlignmentFlag.AlignTop, foot)
        painter.restore()
