"""统一记录结构；所有时间以 UTC ISO 8601 保存。"""

from datetime import datetime, timezone
from typing import TypedDict


CONTENT_TYPES = ("text", "code", "url", "email", "image", "color")
LANGUAGES = ("", "C", "C++", "Python", "SQL", "JavaScript", "Java", "JSON", "HTML", "CSS")


class Item(TypedDict):
    id: str
    text: str
    kind: str
    language: str
    title: str
    notes: str
    tags: list[str]
    group_name: str
    favorite: bool
    pinned: bool
    created_at: str
    last_copied_at: str
    image_path: str
    thumbnail_path: str
    width: int
    height: int
    source: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def normalize_timestamp(value: str) -> str:
    """接受 ISO 时间并归一化；拒绝无时区的导入数据。"""
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise ValueError("时间必须包含时区")
    return moment.astimezone(timezone.utc).isoformat(timespec="microseconds")
