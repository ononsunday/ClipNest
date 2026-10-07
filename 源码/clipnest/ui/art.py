"""主题插画加载与绘制。图片只在界面线程中解码，不参与剪贴板内容处理。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QImageReader, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QWidget


_ASSETS = Path(__file__).resolve().parent.parent / "assets"


def asset_path(name: str) -> Path:
    """源码和 PyInstaller 都使用包内素材目录，不依赖启动时的工作目录。"""
    if not name or Path(name).name != name:
        raise ValueError("素材名称必须是文件名")
    return _ASSETS / name


def _file_key(path: Path) -> tuple[str, int, int] | None:
    try:
        stat = path.stat()
        if path.is_file():
            return str(path), stat.st_mtime_ns, stat.st_size
    except OSError:
        pass
    return None


@lru_cache(maxsize=16)
def _load_art(path: str, modified: int, length: int, limit: int) -> QPixmap:
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    size = reader.size()
    # 用解码器缩放，避免将高分辨率原图长期保留在内存中。
    if size.isValid() and max(size.width(), size.height()) > limit:
        reader.setScaledSize(size.scaled(limit, limit, Qt.AspectRatioMode.KeepAspectRatio))
    image = reader.read()
    if image.isNull():
        return QPixmap()
    return QPixmap.fromImage(image)


def art_pixmap(name: str, max_width: int = 1600) -> QPixmap:
    """返回最长边受限的缓存图片；缺失、不可读或损坏时返回空 QPixmap。"""
    key = _file_key(asset_path(name))
    if key is None:
        return QPixmap()
    limit = max(1, int(max_width))
    # 返回共享副本，调用方更改图片时不会改变缓存。
    return QPixmap(_load_art(*key, limit))


@lru_cache(maxsize=32)
def _nav_icon(path: str, modified: int, length: int, index: int, size: int) -> QIcon:
    from PySide6.QtGui import QRegion

    sheet = _load_art(path, modified, length, 1600)
    if sheet.isNull():
        return QIcon()
    col, row = index % 4, index // 4
    x1, x2 = sheet.width() * col // 4, sheet.width() * (col + 1) // 4
    y1, y2 = sheet.height() * row // 2, sheet.height() * (row + 1) // 2
    cell = sheet.copy(x1, y1, x2 - x1, y2 - y1)
    # 去除格子透明留白，完整保留角色和同格中的表情附件。
    visible = QRegion(cell.mask()).boundingRect()
    if visible.isEmpty():
        return QIcon()
    cell = cell.copy(visible)
    cell = cell.scaled(size * 2, size * 2, Qt.AspectRatioMode.KeepAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)
    cell.setDevicePixelRatio(2.0)
    return QIcon(cell)


def nav_icon(index: int, logical_size: int = 32) -> QIcon:
    """从四列两行的贴纸表中取出侧栏图标，索引顺序先横向后纵向。"""
    if not 0 <= index < 8:
        return QIcon()
    key = _file_key(asset_path("nav_stickers.png"))
    if key is None:
        return QIcon()
    return QIcon(_nav_icon(*key, index, max(1, int(logical_size))))


class ArtWidget(QWidget):
    """随窗口大小绘制插画；不接收鼠标，保留父控件的点击和拖动行为。"""

    def __init__(self, filename: str, mode: str = "cover", focus_y: float = 0.5,
                 radius: float = 10, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if mode not in {"cover", "contain"}:
            raise ValueError("插画模式必须是 cover 或 contain")
        self.filename = filename
        self.mode = mode
        self.focus_y = min(1.0, max(0.0, float(focus_y)))
        self.radius = max(0.0, float(radius))
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAutoFillBackground(False)
        self.setObjectName("artwork")
        self.setStyleSheet("#artwork { background: transparent; border: none; }")
        self._pixmap = art_pixmap(filename)

    def set_art(self, filename: str) -> None:
        self.filename = filename
        self._pixmap = art_pixmap(filename)
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(240, 120)

    def paintEvent(self, event) -> None:
        if self._pixmap.isNull() or self.width() <= 0 or self.height() <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        bounds = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(bounds, self.radius, self.radius)
        painter.setClipPath(clip)
        source = QRectF(self._pixmap.rect())
        if self.mode == "contain":
            scale = min(bounds.width() / source.width(), bounds.height() / source.height())
            target = QRectF(0, 0, source.width() * scale, source.height() * scale)
            target.moveCenter(bounds.center())
        else:
            scale = max(bounds.width() / source.width(), bounds.height() / source.height())
            width, height = bounds.width() / scale, bounds.height() / scale
            source = QRectF((source.width() - width) / 2,
                            (source.height() - height) * self.focus_y, width, height)
            target = bounds
        painter.drawPixmap(target, self._pixmap, source)
