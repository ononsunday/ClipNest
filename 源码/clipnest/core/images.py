"""图片在工作线程中保存为 PNG；Qt GUI 对象由界面线程创建。"""

import hashlib
import os
from pathlib import Path
import uuid

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage, QImageReader


MAX_PIXELS = 40_000_000
MAX_ENCODED_BYTES = 64 * 1024 * 1024
THUMBNAIL_SIZE = 384


def validate_image(image: QImage) -> None:
    if image.isNull() or image.width() <= 0 or image.height() <= 0:
        raise ValueError("图片为空或无法解码")
    if image.width() * image.height() > MAX_PIXELS:
        raise ValueError("图片过大，最多支持 4000 万像素")


def image_digest(image: QImage) -> str:
    """以规范化像素计算去重哈希，忽略 PNG 元数据与输入格式。"""
    validate_image(image)
    normalized = image.convertToFormat(QImage.Format.Format_RGBA8888)
    dimensions = f"{normalized.width()}x{normalized.height()}:".encode("ascii")
    digest = hashlib.sha256(b"image\0" + dimensions)
    digest.update(normalized.constBits())
    return digest.hexdigest()


def decode_image(data: bytes) -> QImage:
    if not data or len(data) > MAX_ENCODED_BYTES:
        raise ValueError("图片数据为空或超过 64 MB")
    payload = QByteArray(data)
    buffer = QBuffer(payload)
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    reader.setAutoTransform(True)
    dimensions = reader.size()
    if not dimensions.isValid() or dimensions.width() * dimensions.height() > MAX_PIXELS:
        raise ValueError("图片尺寸无效或图片过大")
    image = reader.read()
    validate_image(image)
    return image


def _atomic_save(image: QImage, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        if not image.save(str(temporary), "PNG"):
            raise OSError("无法保存剪贴板图片，请检查数据目录权限")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def store_image(image: QImage, directory: Path) -> tuple[str, str, str, int, int]:
    """返回 hash、原图路径、缩略图路径、宽、高。"""
    digest = image_digest(image)
    original = directory / f"{digest}.png"
    thumbnail = directory / "thumbnails" / f"{digest}.png"
    if not original.exists():
        _atomic_save(image, original)
    if not thumbnail.exists():
        small = image.scaled(THUMBNAIL_SIZE, THUMBNAIL_SIZE, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        _atomic_save(small, thumbnail)
    return digest, str(original), str(thumbnail), image.width(), image.height()
