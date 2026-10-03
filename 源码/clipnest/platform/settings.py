"""小型、本地、原子写入的用户设置文件。"""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
from typing import Any


DEFAULTS: dict[str, Any] = {
    "theme": "light",
    "hotkey": "Ctrl+Alt+V",
    "max_items": 2000,
    "max_age_days": 30,
    "excluded_apps": ["1password.exe", "bitwarden.exe", "keepass.exe", "keepassxc.exe", "lastpass.exe", "dashlane.exe", "enpass.exe"],
    "paused": False,
    "startup": False,
    "quick_position": None,
    "close_after_paste": False,
}


def default_data_dir() -> Path:
    """应用数据默认留在当前 Windows 用户的 LocalAppData。"""
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) / "ClipNest" if base else Path.home() / ".clipnest"


def _validated(key: str, value: Any) -> Any:
    if key not in DEFAULTS:
        raise ValueError(f"未知设置：{key}")
    if key == "theme":
        if value not in ("light", "dark"):
            raise ValueError("主题只能是 light 或 dark")
    elif key == "hotkey":
        if not isinstance(value, str) or not value.strip() or len(value) > 80:
            raise ValueError("快捷键不能为空且不能超过 80 个字符")
        value = value.strip()
    elif key in ("max_items", "max_age_days"):
        upper = 100000 if key == "max_items" else 36500
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= upper:
            raise ValueError(f"{key} 必须为 1 至 {upper} 的整数")
    elif key in ("paused", "startup", "close_after_paste"):
        if not isinstance(value, bool):
            raise ValueError(f"{key} 必须为布尔值")
    elif key == "quick_position":
        # Qt 逻辑屏幕坐标允许负数，以支持主屏左侧或上方的显示器。
        if value is not None and (not isinstance(value, list) or len(value) != 2
                or any(isinstance(axis, bool) or not isinstance(axis, int)
                       or not -100000 <= axis <= 100000 for axis in value)):
            raise ValueError("快捷窗口位置必须为空或由两个有效整数坐标组成")
    elif key == "excluded_apps":
        if not isinstance(value, list) or len(value) > 500:
            raise ValueError("排除应用必须是至多 500 项的列表")
        names = []
        for entry in value:
            if not isinstance(entry, str) or not entry.strip() or len(entry) > 260:
                raise ValueError("排除应用应填写可执行文件名，例如 keepass.exe")
            name = entry.strip().casefold()
            if "/" in name or "\\" in name or not name.endswith(".exe"):
                raise ValueError("排除应用应填写可执行文件名，不填写路径")
            if name not in names:
                names.append(name)
        value = names
    return deepcopy(value)


class Settings:
    """加载时逐项验证，损坏文件不阻止软件启动，也不会静默覆盖。"""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "settings.json"
        self.values = deepcopy(DEFAULTS)
        self.load_warning = ""
        if self.path.exists():
            try:
                if self.path.stat().st_size > 1024 * 1024:
                    raise ValueError("设置文件过大")
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(loaded, dict):
                    raise ValueError("设置文件根节点必须为对象")
                for key, value in loaded.items():
                    if key in DEFAULTS:
                        try:
                            self.values[key] = _validated(key, value)
                        except ValueError:
                            self.load_warning = "部分设置无效，已使用默认值；原文件仍然保留。"
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
                self.load_warning = "无法读取设置，已使用默认值；原文件仍然保留。"

    def get(self, key: str, default: Any = None) -> Any:
        return deepcopy(self.values.get(key, default))

    def set(self, key: str, value: Any) -> None:
        self.values[key] = _validated(key, value)

    def save(self) -> None:
        """同目录临时文件 + replace，进程中断不会留下半个 JSON。"""
        validated = {key: _validated(key, self.values[key]) for key in DEFAULTS}
        self.data_dir.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix="settings-", suffix=".tmp", dir=self.data_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(validated, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
            self.values = validated
        finally:
            try:
                Path(name).unlink(missing_ok=True)
            except OSError:
                pass
