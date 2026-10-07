from datetime import datetime
from collections import OrderedDict
from urllib.parse import urlsplit
from PySide6.QtCore import Qt, QSize, QRect, QRectF, QModelIndex, QPersistentModelIndex, Signal, QFileInfo
from PySide6.QtGui import QColor, QPainter, QFont, QPen, QImageReader, QPixmap, QPainterPath
from PySide6.QtWidgets import QApplication, QListWidget, QListWidgetItem, QStyledItemDelegate, QStyle

KINDS = {"text": "文本", "code": "代码", "url": "链接", "email": "邮箱", "image": "图片", "color": "颜色"}


def _delete_rect(rect):
    """删除按钮的绘制与点击检测共用同一组视口坐标。"""
    return QRect(rect.right() - 46, rect.top() + (rect.height() - 32) // 2, 32, 32)


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
    def __init__(self, parent=None):
        super().__init__(parent)
        # 每张仅解码至 80 像素，缓存有上限；不读取大尺寸原图或剪贴板。
        self._thumbnails = OrderedDict()

    def sizeHint(self, option, index):
        return QSize(260, 96)

    def _thumbnail(self, record):
        path = record.get("thumbnail_path", "")
        if not path:
            return QPixmap()
        if path in self._thumbnails:
            self._thumbnails.move_to_end(path)
            return self._thumbnails[path]
        pixmap = QPixmap()
        info = QFileInfo(path)
        # 导入包中的异常缩略图也不能在绘制时触发无限解码。
        if info.isFile() and info.size() <= 2 * 1024 * 1024:
            reader = QImageReader(path)
            reader.setAutoTransform(True)
            dimensions = reader.size()
            if dimensions.isValid() and max(dimensions.width(), dimensions.height()) <= 16000:
                reader.setScaledSize(dimensions.scaled(QSize(80, 80), Qt.AspectRatioMode.KeepAspectRatio))
                pixmap = QPixmap.fromImage(reader.read())
        self._thumbnails[path] = pixmap
        while len(self._thumbnails) > 96:
            self._thumbnails.popitem(last=False)
        return pixmap

    @staticmethod
    def _summary(record):
        kind = record.get("kind", "text")
        text = str(record.get("text", ""))
        lines = [line.strip() for line in text[:600].splitlines() if line.strip()]
        first = lines[0] if lines else ""
        title = record.get("title") or ""
        note = " ".join(str(record.get("notes", ""))[:200].split())
        if kind == "image":
            return title or "图片", note or f"{record.get('width', 0)} × {record.get('height', 0)} 像素"
        if kind == "code":
            language = record.get("language", "")
            return title or (f"{language} 代码" if language else "代码片段"), first
        if kind == "url":
            try:
                domain = urlsplit(text).netloc
            except ValueError:
                domain = ""
            return title or domain or "链接", first
        if title:
            return str(title), first or note
        if kind == "color":
            return first or "颜色", note or "颜色代码"
        if kind == "email":
            return first or "邮箱", note or "邮箱地址"
        return first or "文本", note or " ".join(lines[1:]) or f"文本 · {len(text)} 个字符"

    @staticmethod
    def _type_icon(painter, kind, rect, color):
        """图标直接用矢量绘制，兼容 Windows 字体与高 DPI。"""
        cx, cy = rect.center().x(), rect.center().y()
        painter.setPen(QPen(color, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if kind == "code":
            painter.drawLine(cx - 5, cy - 6, cx - 11, cy)
            painter.drawLine(cx - 11, cy, cx - 5, cy + 6)
            painter.drawLine(cx + 5, cy - 6, cx + 11, cy)
            painter.drawLine(cx + 11, cy, cx + 5, cy + 6)
            painter.drawLine(cx + 2, cy - 8, cx - 2, cy + 8)
        elif kind == "url":
            painter.save()
            painter.translate(cx, cy)
            painter.rotate(-35)
            painter.drawRoundedRect(QRectF(-12, -5, 15, 10), 5, 5)
            painter.drawRoundedRect(QRectF(-3, -5, 15, 10), 5, 5)
            painter.restore()
        elif kind == "email":
            painter.drawRoundedRect(QRectF(cx - 11, cy - 8, 22, 16), 3, 3)
            painter.drawLine(cx - 10, cy - 6, cx, cy + 1)
            painter.drawLine(cx, cy + 1, cx + 10, cy - 6)
        elif kind == "color":
            painter.drawEllipse(QRectF(cx - 9, cy - 9, 18, 18))
            painter.drawLine(cx, cy - 7, cx, cy + 7)
            painter.drawLine(cx - 7, cy, cx + 7, cy)
        elif kind == "image":
            painter.drawRoundedRect(QRectF(cx - 11, cy - 9, 22, 18), 3, 3)
            painter.drawEllipse(QRectF(cx + 2, cy - 5, 3, 3))
            painter.drawLine(cx - 9, cy + 6, cx - 3, cy - 1)
            painter.drawLine(cx - 3, cy - 1, cx + 2, cy + 4)
            painter.drawLine(cx + 2, cy + 4, cx + 6, cy + 1)
            painter.drawLine(cx + 6, cy + 1, cx + 9, cy + 5)
        else:
            painter.drawRoundedRect(QRectF(cx - 8, cy - 11, 16, 22), 3, 3)
            for offset, width in [(-5, 10), (0, 10), (5, 7)]:
                painter.drawLine(cx - 5, cy + offset, cx - 5 + width, cy + offset)

    def paint(self, painter, option, index):
        record = index.data(Qt.ItemDataRole.UserRole) or {}
        dark = bool(QApplication.instance().property("clipnestDark"))
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        view = self.parent()
        bulk = bool(view.property("clipnestBulk")) if view else False
        quick = bool(view.property("singleAction")) if view else False
        hovered_row = bool(option.state & QStyle.StateFlag.State_MouseOver)
        selected_bg = "#15263c" if dark else "#eef0e7" if quick else "#eaf4fc"
        card_bg = "#141414" if dark else "#fffdf8" if quick else "#ffffff"
        muted = QColor("#929dab" if dark else "#889397")
        kind_key = record.get("kind", "text")
        kind = KINDS.get(kind_key, "文本")
        palette = {"text": ("#6f8398", "#eef3f8"), "code": ("#5486b1", "#eaf2fb"),
                   "url": ("#538f86", "#eaf5f1"), "email": ("#8c79a8", "#f3eef9"),
                   "image": ("#a17c53", "#faf1e5"), "color": ("#bd8292", "#faeef2")}
        tint, tile = palette.get(kind_key, palette["text"])
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(option.rect).adjusted(3, 3, -3, -3)
        border = "#2b4b70" if dark and selected else "#c8dfe8" if selected and not quick else "#d8dfcc" if selected else "#26313e" if dark and hovered_row else "#39312a" if dark and quick else "#252525" if dark else "#ece6dc" if quick else "#e7eef3"
        painter.setPen(QPen(QColor(border), 1))
        painter.setBrush(QColor(selected_bg if selected else card_bg))
        painter.drawRoundedRect(rect, 11, 11)
        delete_rect = _delete_rect(option.rect)
        icon_left = rect.left() + (40 if bulk else 12)
        icon_rect = QRectF(icon_left, rect.top() + 16, 36, 36)
        left = icon_rect.right() + 11
        right = delete_rect.left() - 10
        text_width = max(0, int(right - left))
        if bulk:
            checkbox = QRectF(rect.left() + 12, rect.center().y() - 8, 16, 16)
            painter.setPen(QPen(QColor("#356893" if selected and dark else "#2e86e4" if selected else "#77818d"), 1))
            painter.setBrush(QColor("#245b90" if dark else "#2e86e4") if selected else QColor("#141414" if dark else "#ffffff"))
            painter.drawRoundedRect(checkbox, 4, 4)
            if selected:
                painter.setPen(QPen(QColor("#ffffff"), 1.6))
                painter.drawLine(checkbox.left() + 4, checkbox.top() + 8, checkbox.left() + 7, checkbox.top() + 11)
                painter.drawLine(checkbox.left() + 7, checkbox.top() + 11, checkbox.left() + 12, checkbox.top() + 5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#1e2c3b" if dark else tile))
        painter.drawRoundedRect(icon_rect, 9, 9)
        thumbnail = self._thumbnail(record) if kind_key == "image" else QPixmap()
        if thumbnail.isNull():
            self._type_icon(painter, kind_key, icon_rect, QColor("#8da9c9" if dark else tint))
        else:
            painter.save()
            clip = QPainterPath()
            clip.addRoundedRect(icon_rect, 9, 9)
            painter.setClipPath(clip)
            scaled = thumbnail.scaled(QSize(36, 36), Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            source = QRectF((scaled.width() - 36) / 2, (scaled.height() - 36) / 2, 36, 36)
            painter.drawPixmap(icon_rect, scaled, source)
            painter.restore()
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
        title, summary = self._summary(record)
        font = QFont(option.font)
        font.setPointSize(10)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor("#e7edf4" if dark else "#354457"))
        title = painter.fontMetrics().elidedText(" ".join(str(title)[:300].split()), Qt.TextElideMode.ElideRight, text_width)
        painter.drawText(QRectF(left, rect.top() + 10, text_width, 23), Qt.AlignmentFlag.AlignVCenter, title)
        font.setPointSize(9)
        font.setWeight(QFont.Weight.Normal)
        painter.setFont(font)
        painter.setPen(QColor("#a4adb8" if dark else "#74818c"))
        summary = painter.fontMetrics().elidedText(" ".join(str(summary)[:300].split()), Qt.TextElideMode.ElideRight, text_width)
        painter.drawText(QRectF(left, rect.top() + 34, text_width, 21), Qt.AlignmentFlag.AlignVCenter, summary)
        font.setPointSize(8)
        painter.setFont(font)
        painter.setPen(muted)
        stamp = record.get("last_copied_at", "")
        try:
            stamp = datetime.fromisoformat(stamp).astimezone().strftime("%m-%d  %H:%M")
        except (ValueError, TypeError):
            stamp = str(stamp)[:16]
        indicators = (" · 收藏" if record.get("favorite") else "") + (" · 置顶" if record.get("pinned") else "")
        tags = record.get("tags", []) or []
        suffix = " · " + ", ".join(tags[:2]) if tags else ""
        foot = painter.fontMetrics().elidedText(kind + " · " + stamp + indicators + suffix, Qt.TextElideMode.ElideRight, text_width)
        painter.drawText(QRectF(left, rect.top() + 61, text_width, 18), Qt.AlignmentFlag.AlignVCenter, foot)
        painter.restore()
