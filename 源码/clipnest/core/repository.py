"""SQLite 数据仓库：连接不跨线程共享，事务中完成去重与状态更新。"""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from functools import wraps
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sqlite3
import threading
import time
from typing import Iterator
import uuid
import zipfile

from PySide6.QtGui import QImage

from .classify import detect
from .images import MAX_ENCODED_BYTES, decode_image, store_image
from .models import CONTENT_TYPES, LANGUAGES, Item, normalize_timestamp, utc_now


SCHEMA_VERSION = 2
_MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
_MAX_ARCHIVE_ITEMS = 50_000
_EDITABLE_FIELDS = {"text", "kind", "language", "title", "notes", "tags", "group_name", "favorite", "pinned", "created_at", "last_copied_at", "source"}
_IMAGE_LOCKS: dict[str, threading.RLock] = {}
_IMAGE_LOCKS_GUARD = threading.Lock()


def _serialized_images(method):
    """同一数据目录的图片写入/删除/导出不能交错，避免悬空图片路径。"""
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._image_lock:
            return method(self, *args, **kwargs)
    return wrapper


class Repository:
    def __init__(self, db_path: Path, image_dir: Path):
        self.db_path = Path(db_path).resolve()
        self.image_dir = Path(image_dir).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.image_dir.mkdir(parents=True, exist_ok=True)
        with _IMAGE_LOCKS_GUARD:
            self._image_lock = _IMAGE_LOCKS.setdefault(str(self.image_dir), threading.RLock())
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=15.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError("数据库由更新版本的 ClipNest 创建，请升级软件")
            if version < 1:
                connection.executescript("""
                    BEGIN IMMEDIATE;
                    CREATE TABLE IF NOT EXISTS items (
                        id TEXT PRIMARY KEY,
                        content_hash TEXT NOT NULL UNIQUE,
                        text TEXT NOT NULL DEFAULT '',
                        kind TEXT NOT NULL,
                        language TEXT NOT NULL DEFAULT '',
                        title TEXT NOT NULL DEFAULT '',
                        notes TEXT NOT NULL DEFAULT '',
                        tags TEXT NOT NULL DEFAULT '[]',
                        group_name TEXT NOT NULL DEFAULT '',
                        favorite INTEGER NOT NULL DEFAULT 0 CHECK(favorite IN (0, 1)),
                        pinned INTEGER NOT NULL DEFAULT 0 CHECK(pinned IN (0, 1)),
                        created_at TEXT NOT NULL,
                        last_copied_at TEXT NOT NULL,
                        image_path TEXT NOT NULL DEFAULT '',
                        thumbnail_path TEXT NOT NULL DEFAULT '',
                        width INTEGER NOT NULL DEFAULT 0,
                        height INTEGER NOT NULL DEFAULT 0,
                        source TEXT NOT NULL DEFAULT ''
                    );
                    CREATE INDEX IF NOT EXISTS idx_items_recent ON items(pinned DESC, last_copied_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_items_kind ON items(kind);
                    CREATE TABLE IF NOT EXISTS groups (name TEXT PRIMARY KEY);
                    CREATE TABLE IF NOT EXISTS item_tags (
                        item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
                        tag TEXT NOT NULL,
                        PRIMARY KEY(item_id, tag)
                    );
                    CREATE INDEX IF NOT EXISTS idx_item_tags_tag ON item_tags(tag);
                    PRAGMA user_version = 1;
                    COMMIT;
                """)
            if version < 2:
                connection.executescript("""
                    BEGIN IMMEDIATE;
                    CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
                        text, title, notes, tags, group_name,
                        content='items', content_rowid='rowid', tokenize='unicode61'
                    );
                    CREATE TRIGGER IF NOT EXISTS items_ai AFTER INSERT ON items BEGIN
                        INSERT INTO items_fts(rowid, text, title, notes, tags, group_name)
                        VALUES(new.rowid, new.text, new.title, new.notes, new.tags, new.group_name);
                    END;
                    CREATE TRIGGER IF NOT EXISTS items_ad AFTER DELETE ON items BEGIN
                        INSERT INTO items_fts(items_fts, rowid, text, title, notes, tags, group_name)
                        VALUES('delete', old.rowid, old.text, old.title, old.notes, old.tags, old.group_name);
                    END;
                    CREATE TRIGGER IF NOT EXISTS items_au AFTER UPDATE ON items BEGIN
                        INSERT INTO items_fts(items_fts, rowid, text, title, notes, tags, group_name)
                        VALUES('delete', old.rowid, old.text, old.title, old.notes, old.tags, old.group_name);
                        INSERT INTO items_fts(rowid, text, title, notes, tags, group_name)
                        VALUES(new.rowid, new.text, new.title, new.notes, new.tags, new.group_name);
                    END;
                    INSERT INTO items_fts(items_fts) VALUES('rebuild');
                    PRAGMA user_version = 2;
                    COMMIT;
                """)

    @staticmethod
    def _item(row: sqlite3.Row | None) -> Item | None:
        if row is None:
            return None
        item = dict(row)
        item.pop("content_hash", None)
        item["tags"] = json.loads(item["tags"])
        item["favorite"] = bool(item["favorite"])
        item["pinned"] = bool(item["pinned"])
        return item

    @staticmethod
    def _text_hash(text: str) -> str:
        return hashlib.sha256(b"text\0" + text.encode("utf-8")).hexdigest()

    def add_text(self, text: str, source: str = "") -> Item:
        if not isinstance(text, str) or not text:
            raise ValueError("不能保存空文本")
        kind, language = detect(text)
        return self._add(self._text_hash(text), text=text, kind=kind, language=language, source=source)

    @_serialized_images
    def add_image(self, image: QImage, source: str = "") -> Item:
        digest, original, thumbnail, width, height = store_image(image, self.image_dir)
        return self._add(digest, kind="image", image_path=original, thumbnail_path=thumbnail, width=width, height=height, source=source)

    def _add(self, digest: str, **data) -> Item:
        now = utc_now()
        record = {
            "id": uuid.uuid4().hex, "content_hash": digest, "text": "", "kind": "text", "language": "", "title": "", "notes": "", "tags": "[]", "group_name": "", "favorite": 0, "pinned": 0, "created_at": now, "last_copied_at": now, "image_path": "", "thumbnail_path": "", "width": 0, "height": 0, "source": "",
        }
        record.update(data)
        with self._connection() as connection:
            # UPSERT 保留收藏、置顶、手动分类和备注，仅刷新使用时间及来源。
            columns = tuple(record)
            connection.execute(
                f"INSERT INTO items ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)}) "
                "ON CONFLICT(content_hash) DO UPDATE SET last_copied_at=excluded.last_copied_at, "
                "source=CASE WHEN excluded.source<>'' THEN excluded.source ELSE items.source END",
                tuple(record[column] for column in columns),
            )
            return self._item(connection.execute("SELECT * FROM items WHERE content_hash=?", (digest,)).fetchone())

    def get(self, id: str) -> Item | None:
        with self._connection() as connection:
            return self._item(connection.execute("SELECT * FROM items WHERE id=?", (id,)).fetchone())

    def search(self, query: str = "", kind: str = "", favorite: bool = False, pinned: bool = False, tag: str = "", group: str = "", since: str = "", limit: int = 200, offset: int = 0) -> list[Item]:
        clauses, parameters = [], []
        query = query.strip()
        if query:
            # MATCH 不直接拼接用户输入；词组全部引用，符号用 LIKE 字面匹配补充。
            tokens = re.findall(r"\w+", query, re.UNICODE)[:32]
            pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            substring = " OR ".join(f"{column} LIKE ? ESCAPE '\\'" for column in ("text", "title", "notes", "tags", "group_name"))
            if tokens:
                expression = " AND ".join('"' + token.replace('"', '""') + '"' for token in tokens)
                clauses.append(f"(rowid IN (SELECT rowid FROM items_fts WHERE items_fts MATCH ?) OR {substring})")
                parameters.append(expression)
            else:
                clauses.append(f"({substring})")
            parameters.extend([pattern] * 5)
        for column, value in (("kind", kind), ("group_name", group)):
            if value:
                clauses.append(f"{column}=?")
                parameters.append(value)
        if favorite:
            clauses.append("favorite=1")
        if pinned:
            clauses.append("pinned=1")
        if tag:
            clauses.append("id IN (SELECT item_id FROM item_tags WHERE tag=?)")
            parameters.append(tag)
        if since:
            clauses.append("last_copied_at>=?")
            parameters.append(normalize_timestamp(since))
        if limit < 0 or offset < 0:
            raise ValueError("分页参数不能为负数")
        parameters.extend((min(int(limit), 50000), int(offset)))
        sql = "SELECT * FROM items"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY pinned DESC, last_copied_at DESC, id DESC LIMIT ? OFFSET ?"
        with self._connection() as connection:
            return [self._item(row) for row in connection.execute(sql, parameters)]

    @staticmethod
    def _normalize_tags(tags) -> list[str]:
        if isinstance(tags, str):
            tags = tags.replace("，", ",").split(",")
        if not isinstance(tags, (list, tuple)) or any(not isinstance(tag, str) for tag in tags):
            raise ValueError("标签必须是字符串列表")
        normalized = list(dict.fromkeys(tag.strip() for tag in tags if tag.strip()))
        if len(normalized) > 100 or any(len(tag) > 64 for tag in normalized):
            raise ValueError("每条记录最多 100 个标签，每个标签最多 64 个字符")
        return normalized

    def update(self, id: str, **fields) -> Item | None:
        if "group" in fields:
            fields["group_name"] = fields.pop("group")
        if set(fields) - _EDITABLE_FIELDS:
            raise ValueError("存在不能编辑的记录字段")
        if "kind" in fields and fields["kind"] not in CONTENT_TYPES:
            raise ValueError("未知内容类型")
        if "language" in fields and fields["language"] not in LANGUAGES:
            raise ValueError("不支持的代码语言")
        tags = None
        if "tags" in fields:
            tags = self._normalize_tags(fields["tags"])
            fields["tags"] = json.dumps(tags, ensure_ascii=False)
        for name in ("created_at", "last_copied_at"):
            if name in fields:
                fields[name] = normalize_timestamp(fields[name])
        for name in ("favorite", "pinned"):
            if name in fields:
                fields[name] = int(bool(fields[name]))
        for name in ("title", "notes", "source", "group_name", "text"):
            if name in fields and not isinstance(fields[name], str):
                raise ValueError("文本字段必须为字符串")
        if "group_name" in fields:
            fields["group_name"] = fields["group_name"].strip()
            if len(fields["group_name"]) > 64:
                raise ValueError("分组名称最多 64 个字符")
        with self._connection() as connection:
            current = connection.execute("SELECT * FROM items WHERE id=?", (id,)).fetchone()
            if current is None:
                return None
            if fields.get("kind") == "image" and current["kind"] != "image":
                raise ValueError("纯文本不能改为图片")
            if current["kind"] == "image" and "kind" in fields and fields["kind"] != "image":
                raise ValueError("图片不能改为文本类型")
            if "text" in fields and current["kind"] != "image":
                if not fields["text"]:
                    raise ValueError("不能保存空文本")
                fields["content_hash"] = self._text_hash(fields["text"])
            if fields:
                try:
                    connection.execute("UPDATE items SET " + ",".join(f"{column}=?" for column in fields) + " WHERE id=?", (*fields.values(), id))
                except sqlite3.IntegrityError as exc:
                    raise ValueError("相同内容已存在，不能编辑成重复记录") from exc
            if tags is not None:
                connection.execute("DELETE FROM item_tags WHERE item_id=?", (id,))
                connection.executemany("INSERT INTO item_tags(item_id,tag) VALUES (?,?)", [(id, tag) for tag in tags])
            if fields.get("group_name"):
                connection.execute("INSERT OR IGNORE INTO groups(name) VALUES (?)", (fields["group_name"],))
            return self._item(connection.execute("SELECT * FROM items WHERE id=?", (id,)).fetchone())

    @_serialized_images
    def _remove(self, where: str, parameters: tuple = ()) -> int:
        with self._connection() as connection:
            paths = connection.execute("SELECT image_path, thumbnail_path FROM items WHERE " + where, parameters).fetchall()
            count = connection.execute("DELETE FROM items WHERE " + where, parameters).rowcount
        self._remove_image_files(paths)
        return count

    def _remove_image_files(self, rows) -> None:
        # 只移除自己数据目录中的文件；数据库中的外部路径永远不删除。
        with self._connection() as connection:
            referenced = {str(Path(row[0]).resolve()) for row in connection.execute("SELECT image_path FROM items WHERE image_path<>''")}
            referenced.update(str(Path(row[0]).resolve()) for row in connection.execute("SELECT thumbnail_path FROM items WHERE thumbnail_path<>''"))
        for row in rows:
            for value in row:
                if not value:
                    continue
                path = Path(value).resolve()
                if path.is_relative_to(self.image_dir) and str(path) not in referenced:
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        # 文件占用不应回滚已经完成的数据删除；下次维护可再处理。
                        pass

    def delete(self, id: str) -> bool:
        return bool(self._remove("id=?", (id,)))

    @_serialized_images
    def delete_many(self, ids, include_protected: bool = True) -> int:
        """在一次事务中批量删除；提交后再清理没有其他引用的图片。"""
        if isinstance(ids, (str, bytes)):
            raise ValueError("批量删除需要记录 ID 列表")
        if not isinstance(include_protected, bool):
            raise ValueError("保护记录选项必须为布尔值")
        try:
            values = list(ids)
        except TypeError as exc:
            raise ValueError("批量删除需要记录 ID 列表") from exc
        if any(not isinstance(value, str) or not value for value in values):
            raise ValueError("记录 ID 必须是非空字符串")
        unique = list(dict.fromkeys(values))
        if not unique:
            return 0
        paths, count = [], 0
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            # 每批低于旧版 SQLite 的参数上限，但所有批次共享同一个事务。
            for start in range(0, len(unique), 800):
                chunk = unique[start:start + 800]
                where = "id IN (" + ",".join("?" for _ in chunk) + ")"
                if not include_protected:
                    # 以提交时的数据库状态保护，避免界面尚未刷新时误删刚收藏的内容。
                    where += " AND favorite=0 AND pinned=0"
                paths.extend(connection.execute("SELECT image_path, thumbnail_path FROM items WHERE " + where, chunk).fetchall())
                count += connection.execute("DELETE FROM items WHERE " + where, chunk).rowcount
        self._remove_image_files(paths)
        return count

    @_serialized_images
    def clear_history(self, include_protected: bool = False) -> int:
        deleted = self._remove("1=1" if include_protected else "favorite=0 AND pinned=0")
        if include_protected:
            self._prune_orphan_images(min_age_seconds=0)
        return deleted

    @_serialized_images
    def cleanup(self, max_items: int = 2000, max_age_days: int = 30) -> int:
        if max_items < 0 or max_age_days < 0:
            raise ValueError("保留限制不能为负数")
        deleted = 0
        if max_age_days:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat(timespec="microseconds")
            deleted += self._remove("favorite=0 AND pinned=0 AND last_copied_at<?", (cutoff,))
        if max_items:
            deleted += self._remove("id IN (SELECT id FROM items WHERE favorite=0 AND pinned=0 ORDER BY last_copied_at DESC,id DESC LIMIT -1 OFFSET ?)", (max_items,))
        self._prune_orphan_images(min_age_seconds=3600)
        return deleted

    @_serialized_images
    def _prune_orphan_images(self, min_age_seconds: int) -> int:
        """只清理本软件的哈希命名 PNG，不递归、不跟随符号链接。"""
        with self._connection() as connection:
            referenced = set()
            for row in connection.execute("SELECT image_path,thumbnail_path FROM items"):
                referenced.update(str(Path(value).resolve()) for value in row if value)
        cutoff = time.time() - min_age_seconds
        removed = 0
        for directory in (self.image_dir, self.image_dir / "thumbnails"):
            # 避免 thumbnails 被改成链接后进入其他目录清理文件。
            if directory.is_symlink() or not directory.is_dir() or directory.resolve() != directory:
                continue
            try:
                candidates = list(directory.iterdir())
            except OSError:
                continue
            for candidate in candidates:
                if not re.fullmatch(r"[0-9a-f]{64}\.png", candidate.name):
                    continue
                try:
                    if candidate.is_symlink() or not candidate.is_file():
                        continue
                    resolved = candidate.resolve()
                    if not resolved.is_relative_to(self.image_dir) or str(resolved) in referenced:
                        continue
                    if min_age_seconds and candidate.stat().st_mtime > cutoff:
                        continue
                    candidate.unlink()
                    removed += 1
                except OSError:
                    # 被图片查看器占用的文件留到下次维护；不影响历史清理成功。
                    continue
        return removed

    def stats(self) -> dict:
        with self._connection() as connection:
            row = connection.execute("SELECT COUNT(*) total, COALESCE(SUM(favorite),0) favorites, COALESCE(SUM(pinned),0) pinned FROM items").fetchone()
            return dict(row)

    def groups(self) -> list[str]:
        with self._connection() as connection:
            return [row[0] for row in connection.execute("SELECT name FROM groups ORDER BY name COLLATE NOCASE")]

    def tags(self) -> list[str]:
        with self._connection() as connection:
            return [row[0] for row in connection.execute("SELECT DISTINCT tag FROM item_tags ORDER BY tag COLLATE NOCASE")]

    def create_group(self, name: str) -> None:
        name = name.strip()
        if not name or len(name) > 64:
            raise ValueError("请输入 1 到 64 个字符的分组名称")
        with self._connection() as connection:
            connection.execute("INSERT OR IGNORE INTO groups(name) VALUES (?)", (name,))

    @_serialized_images
    def export_archive(self, path: Path) -> None:
        """导出 ZIP 归档；不复制数据库文件，避免 WAL 快照不完整。"""
        path = Path(path).resolve()
        database_files = {self.db_path, Path(str(self.db_path) + "-wal"), Path(str(self.db_path) + "-shm")}
        if path in database_files or path.is_relative_to(self.image_dir):
            raise ValueError("备份不能覆盖正在使用的数据库或图片目录")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with self._connection() as connection:
                connection.execute("BEGIN")
                items = [self._item(row) for row in connection.execute("SELECT * FROM items ORDER BY created_at,id")]
                groups = [row[0] for row in connection.execute("SELECT name FROM groups ORDER BY name")]
                with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    added = set()
                    for item in items:
                        if item["image_path"]:
                            image = Path(item["image_path"]).resolve()
                            if not image.is_relative_to(self.image_dir) or not image.is_file():
                                raise ValueError("图片文件缺失或不在本地数据目录，导出已停止")
                            asset = f"images/{image.name}"
                            item["image_asset"] = asset
                            if asset not in added:
                                archive.write(image, asset)
                                added.add(asset)
                        # 导出不泄露本机绝对数据目录；导入会重建路径。
                        item["image_path"] = ""
                        item["thumbnail_path"] = ""
                    archive.writestr("manifest.json", json.dumps({"format": "clipnest", "version": 1, "items": items, "groups": groups}, ensure_ascii=False))
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    @_serialized_images
    def import_archive(self, path: Path) -> int:
        """先校验全部记录和图片，再合并；不向任意归档路径写入文件。"""
        validated, groups = self._validate_archive(Path(path))
        stored_images = []
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                for item, image in validated:
                    if image is not None:
                        digest, original, thumbnail, width, height = store_image(image, self.image_dir)
                        stored_images.append((original, thumbnail))
                    else:
                        digest, original, thumbnail, width, height = self._text_hash(item["text"]), "", "", 0, 0
                    previous = self._item(connection.execute("SELECT * FROM items WHERE content_hash=?", (digest,)).fetchone())
                    record = {key: item[key] for key in _EDITABLE_FIELDS}
                    if previous:
                        # 合并时不会失去已有收藏与标签，也不会把导入时间当作复制时间。
                        record["created_at"] = min(previous["created_at"], item["created_at"])
                        record["last_copied_at"] = max(previous["last_copied_at"], item["last_copied_at"])
                        record["favorite"] = previous["favorite"] or item["favorite"]
                        record["pinned"] = previous["pinned"] or item["pinned"]
                        record["tags"] = list(dict.fromkeys(previous["tags"] + item["tags"]))
                    record.update(id=previous["id"] if previous else uuid.uuid4().hex, content_hash=digest, image_path=original, thumbnail_path=thumbnail, width=width, height=height)
                    tag_values = record["tags"]
                    record["tags"] = json.dumps(tag_values, ensure_ascii=False)
                    columns = tuple(record)
                    connection.execute(f"INSERT INTO items ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)}) ON CONFLICT(content_hash) DO UPDATE SET " + ",".join(f"{column}=excluded.{column}" for column in columns if column not in {"id", "content_hash"}), tuple(record.values()))
                    connection.execute("DELETE FROM item_tags WHERE item_id=?", (record["id"],))
                    connection.executemany("INSERT INTO item_tags(item_id,tag) VALUES (?,?)", [(record["id"], tag) for tag in tag_values])
                    if record["group_name"]:
                        connection.execute("INSERT OR IGNORE INTO groups(name) VALUES (?)", (record["group_name"],))
                connection.executemany("INSERT OR IGNORE INTO groups(name) VALUES (?)", [(group.strip(),) for group in groups])
            return len(validated)
        except Exception:
            # 数据事务失败时，仅清理本次写入且未被任何记录引用的图片。
            self._remove_image_files(stored_images)
            raise

    def _validate_archive(self, path: Path) -> tuple[list[tuple[dict, QImage | None]], list[str]]:
        try:
            with zipfile.ZipFile(path) as archive:
                members = archive.infolist()
                names = [entry.filename for entry in members]
                if len(names) != len(set(names)):
                    raise ValueError("归档包含重复文件名")
                if sum(entry.file_size for entry in members) > _MAX_ARCHIVE_BYTES:
                    raise ValueError("解压后的归档超过 512 MB")
                for entry in members:
                    # Python zipfile 在 Windows 读取时会归一化反斜线，必须检查原始名称。
                    original_name = entry.orig_filename
                    name = PurePosixPath(original_name)
                    if name.is_absolute() or ".." in name.parts or "\\" in original_name or ":" in original_name:
                        raise ValueError("归档包含不安全的文件路径")
                if "manifest.json" not in names:
                    raise ValueError("归档缺少 manifest.json")
                if archive.getinfo("manifest.json").file_size > 32 * 1024 * 1024:
                    raise ValueError("归档清单过大")
                manifest = json.loads(archive.read("manifest.json"))
                if not isinstance(manifest, dict) or manifest.get("format") != "clipnest" or manifest.get("version") != 1:
                    raise ValueError("不支持的 ClipNest 归档格式")
                items = manifest.get("items")
                groups = manifest.get("groups", [])
                if not isinstance(items, list) or len(items) > _MAX_ARCHIVE_ITEMS:
                    raise ValueError("归档记录列表无效或超过 50000 条")
                if not isinstance(groups, list) or any(not isinstance(group, str) or not group.strip() or len(group) > 64 for group in groups):
                    raise ValueError("归档分组无效")
                validated = []
                decoded_bytes = 0
                for raw in items:
                    if not isinstance(raw, dict):
                        raise ValueError("归档记录结构无效")
                    item = dict(raw)
                    for key in ("text", "kind", "language", "title", "notes", "group_name", "created_at", "last_copied_at", "source"):
                        item.setdefault(key, "")
                        if not isinstance(item[key], str):
                            raise ValueError("归档包含无效文本字段")
                    if item["kind"] not in CONTENT_TYPES or item["language"] not in LANGUAGES:
                        raise ValueError("归档内容类型或代码语言无效")
                    if len(item["group_name"]) > 64:
                        raise ValueError("归档分组名称过长")
                    item["group_name"] = item["group_name"].strip()
                    for key in ("favorite", "pinned"):
                        item.setdefault(key, False)
                        if not isinstance(item[key], bool):
                            raise ValueError("归档收藏或置顶字段无效")
                    item["tags"] = self._normalize_tags(item.get("tags", []))
                    item["created_at"] = normalize_timestamp(item["created_at"])
                    item["last_copied_at"] = normalize_timestamp(item["last_copied_at"])
                    image = None
                    if item["kind"] == "image":
                        asset = item.get("image_asset", "")
                        if not isinstance(asset, str) or not re.fullmatch(r"images/[0-9a-f]{64}\.png", asset) or asset not in names:
                            raise ValueError("归档图片路径无效或文件缺失")
                        if archive.getinfo(asset).file_size > MAX_ENCODED_BYTES:
                            raise ValueError("归档图片过大")
                        image = decode_image(archive.read(asset))
                        decoded_bytes += image.sizeInBytes()
                        if decoded_bytes > _MAX_ARCHIVE_BYTES:
                            raise ValueError("归档解码后的图片超过 512 MB，请分批导入")
                    elif not item["text"]:
                        raise ValueError("归档包含空文本记录")
                    validated.append((item, image))
                return validated, groups
        except (zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError, RecursionError) as exc:
            raise ValueError("无法读取 ClipNest 归档，文件可能已损坏") from exc
