"""验证真实 SQLite 文件、线程与图片归档，不用假数据仓库。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import time
import zipfile

from PySide6.QtGui import QColor, QImage
import pytest

from clipnest.core.repository import Repository, SCHEMA_VERSION


@pytest.fixture
def repository(tmp_path):
    return Repository(tmp_path / "history.sqlite3", tmp_path / "images")


def test_preserves_text_dedup_and_manual_metadata(repository):
    original = "\tdef hello():\r\n    print('你好')\r\n"
    first = repository.add_text(original, "editor.exe")
    repository.update(first["id"], favorite=True, pinned=True, kind="text", language="", title="测试", tags=["Python", "Python", "学习"], notes="备注", group_name="资料")
    second = repository.add_text(original)
    assert second["id"] == first["id"]
    assert second["text"] == original
    assert second["kind"] == "text"
    assert second["source"] == "editor.exe"
    assert second["favorite"] and second["pinned"]
    assert second["tags"] == ["Python", "学习"]
    assert second["created_at"] == first["created_at"]
    assert second["last_copied_at"] >= first["last_copied_at"]
    assert repository.stats() == {"total": 1, "favorites": 1, "pinned": 1}


def test_exact_whitespace_content_is_distinct(repository):
    assert repository.add_text("a\nb")["id"] != repository.add_text("a b")["id"]


def test_chinese_substring_search_and_fts_words(repository):
    chinese = repository.add_text("今天学习数据库和剪贴板")
    code = repository.add_text("SELECT name, age FROM users WHERE age > 18;")
    repository.update(code["id"], title="查询示例", notes="按年龄过滤")
    assert repository.search("数据库")[0]["id"] == chinese["id"]
    assert repository.search("SELECT users")[0]["id"] == code["id"]
    assert repository.search("年龄")[0]["id"] == code["id"]


@pytest.mark.parametrize("query", ["\"", "' OR 1=1 --", "*", "-", "%", "_", "\\", "x:y", 'SELECT * FROM users;', '(abc) OR "foo"', "a%_b"])
def test_query_symbols_are_safe_and_literal_fallback_works(repository, query):
    item = repository.add_text("前缀 " + query + " 后缀")
    assert any(result["id"] == item["id"] for result in repository.search(query))
    assert repository.stats()["total"] == 1


def test_like_wildcards_do_not_match_unrelated_text(repository):
    repository.add_text("普通文本")
    assert repository.search("%") == []
    assert repository.search("_") == []


def test_filters_pagination_and_pin_order(repository):
    ids = [repository.add_text(f"item {index}")["id"] for index in range(5)]
    repository.update(ids[0], favorite=True, pinned=True, tags=["学习"], group="课程")
    assert repository.search(limit=1)[0]["id"] == ids[0]
    assert repository.search(favorite=True, pinned=True, tag="学习", group="课程")[0]["id"] == ids[0]
    assert repository.search(tag="不存在") == []
    assert len(repository.search(limit=2, offset=2)) == 2
    assert repository.groups() == ["课程"]
    assert repository.tags() == ["学习"]
    assert repository.search(since=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat()) == []


def test_update_rejects_unknown_fields_and_duplicate_edits(repository):
    first = repository.add_text("first")
    second = repository.add_text("second")
    with pytest.raises(ValueError):
        repository.update(first["id"], image_path="C:/outside.png")
    with pytest.raises(ValueError):
        repository.update(second["id"], text="first")
    assert repository.get(second["id"])["text"] == "second"


def test_delete_updates_fts_and_tags(repository):
    item = repository.add_text("needle unique")
    repository.update(item["id"], tags=["unique"])
    assert repository.delete(item["id"])
    assert not repository.delete(item["id"])
    assert repository.search("needle") == []
    assert repository.tags() == []


def test_retention_protects_favorites_pins_and_uses_last_copy(repository):
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    ordinary = repository.add_text("过期")
    favorite = repository.add_text("收藏")
    pinned = repository.add_text("置顶")
    recent_copy = repository.add_text("老内容刚复制")
    repository.update(ordinary["id"], created_at=old, last_copied_at=old)
    repository.update(favorite["id"], favorite=True, last_copied_at=old)
    repository.update(pinned["id"], pinned=True, last_copied_at=old)
    repository.update(recent_copy["id"], created_at=old)
    assert repository.cleanup(max_items=2000, max_age_days=30) == 1
    assert repository.get(favorite["id"])
    assert repository.get(pinned["id"])
    assert repository.get(recent_copy["id"])
    assert repository.clear_history() == 1
    assert repository.clear_history(include_protected=True) == 2


def test_count_retention_caps_only_ordinary_items(repository):
    favorite = repository.add_text("favorite")
    repository.update(favorite["id"], favorite=True)
    for index in range(5):
        repository.add_text(str(index))
    assert repository.cleanup(max_items=2, max_age_days=0) == 3
    assert repository.stats()["total"] == 3
    assert repository.cleanup(max_items=0, max_age_days=0) == 0


def test_concurrent_text_writes_deduplicate(repository):
    with ThreadPoolExecutor(max_workers=8) as pool:
        items = list(pool.map(repository.add_text, [f"same {index % 5}" for index in range(100)]))
    assert len({item["id"] for item in items}) == 5
    assert repository.stats()["total"] == 5


def test_schema_persists_and_reopens(repository):
    item = repository.add_text("persist")
    reloaded = Repository(repository.db_path, repository.image_dir)
    assert reloaded.get(item["id"])["text"] == "persist"
    with sqlite3.connect(repository.db_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_schema_migration_rebuilds_existing_fts(repository):
    item = repository.add_text("迁移可搜索")
    with sqlite3.connect(repository.db_path) as connection:
        connection.executescript("DROP TRIGGER items_ai; DROP TRIGGER items_ad; DROP TRIGGER items_au; DROP TABLE items_fts; PRAGMA user_version=1;")
    migrated = Repository(repository.db_path, repository.image_dir)
    assert migrated.search("可搜索")[0]["id"] == item["id"]


def test_image_record_dedup_thumbnail_and_delete(repository):
    image = QImage(600, 300, QImage.Format.Format_ARGB32)
    image.fill(QColor("#5599dd"))
    first = repository.add_image(image)
    second = repository.add_image(image.convertToFormat(QImage.Format.Format_RGB32))
    assert first["id"] == second["id"]
    assert (first["width"], first["height"]) == (600, 300)
    thumbnail = QImage(first["thumbnail_path"])
    assert (thumbnail.width(), thumbnail.height()) == (384, 192)
    assert repository.delete(first["id"])
    assert not Path(first["image_path"]).exists()
    assert not Path(first["thumbnail_path"]).exists()


def test_export_import_round_trip_text_image_metadata(repository, tmp_path):
    text = repository.add_text("  SELECT * FROM 中文;\r\n")
    old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    updated = repository.update(text["id"], title="原始代码", notes="备注", favorite=True, pinned=True, tags=["SQL", "课程"], group_name="资料", created_at=old, last_copied_at=old)
    repository.create_group("空分组")
    image = QImage(20, 10, QImage.Format.Format_ARGB32)
    image.fill(QColor("red"))
    repository.add_image(image)
    archive = tmp_path / "history.zip"
    repository.export_archive(archive)
    imported = Repository(tmp_path / "copy.sqlite3", tmp_path / "copy_images")
    assert imported.import_archive(archive) == 2
    restored = imported.search("中文")[0]
    for key in ("text", "kind", "language", "title", "notes", "favorite", "pinned", "tags", "group_name", "created_at", "last_copied_at"):
        assert restored[key] == updated[key]
    assert imported.groups() == ["空分组", "资料"]
    assert imported.search(kind="image")[0]["image_path"].startswith(str(imported.image_dir))
    assert imported.import_archive(archive) == 2
    assert imported.stats()["total"] == 2


def _write_archive(path, items=None, extra=None):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"format": "clipnest", "version": 1, "items": items or [], "groups": []}))
        if extra:
            # ZipInfo 在 Windows 构造时会把反斜线改为正斜线；显式保留攻击者原名。
            info = zipfile.ZipInfo()
            info.filename = extra
            archive.writestr(info, b"no")


@pytest.mark.parametrize("unsafe", ["../outside.png", "images/../../outside.png", "/absolute.png", "C:/outside.png", "images\\evil.png"])
def test_archive_rejects_all_traversal_names(repository, tmp_path, unsafe):
    archive = tmp_path / "malicious.zip"
    _write_archive(archive, extra=unsafe)
    with pytest.raises(ValueError, match="路径"):
        repository.import_archive(archive)
    assert repository.stats()["total"] == 0
    assert not (tmp_path / "outside.png").exists()


def test_archive_preflight_does_not_partially_import(repository, tmp_path):
    valid = dict(repository.add_text("valid"))
    repository.clear_history(True)
    bad = dict(valid, text="bad", created_at="invalid-date")
    archive = tmp_path / "invalid.zip"
    _write_archive(archive, items=[valid, bad])
    with pytest.raises(ValueError):
        repository.import_archive(archive)
    assert repository.stats()["total"] == 0


def test_archive_rejects_invalid_image_bytes(repository, tmp_path):
    archive = tmp_path / "invalid-image.zip"
    item = dict(repository.add_text("placeholder"), text="", kind="image", image_asset="images/" + "0" * 64 + ".png")
    repository.clear_history(True)
    _write_archive(archive, items=[item], extra=item["image_asset"])
    with pytest.raises(ValueError):
        repository.import_archive(archive)
    assert repository.stats()["total"] == 0


def test_delete_never_removes_external_image_path(repository, tmp_path):
    item = repository.add_text("tampered database")
    external = tmp_path / "user_file.png"
    external.write_bytes(b"user content")
    with sqlite3.connect(repository.db_path) as connection:
        connection.execute("UPDATE items SET image_path=? WHERE id=?", (str(external), item["id"]))
    repository.delete(item["id"])
    assert external.read_bytes() == b"user content"


def test_export_never_overwrites_live_database_or_images(repository):
    item = repository.add_text("database must survive")
    with pytest.raises(ValueError, match="覆盖"):
        repository.export_archive(repository.db_path)
    with pytest.raises(ValueError, match="覆盖"):
        repository.export_archive(repository.image_dir / "backup.zip")
    assert repository.get(item["id"])["text"] == "database must survive"


def test_failed_import_rolls_back_rows_and_unreferenced_image_files(repository, tmp_path):
    image = QImage(10, 10, QImage.Format.Format_ARGB32)
    image.fill(QColor("blue"))
    repository.add_image(image)
    repository.add_text("force failure")
    archive = tmp_path / "atomic.zip"
    repository.export_archive(archive)
    target = Repository(tmp_path / "atomic.sqlite3", tmp_path / "atomic-images")
    with sqlite3.connect(target.db_path) as connection:
        connection.executescript("CREATE TRIGGER injected_failure BEFORE INSERT ON items WHEN new.text='force failure' BEGIN SELECT RAISE(ABORT,'injected failure'); END;")
    with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
        target.import_archive(archive)
    assert target.stats()["total"] == 0
    assert list(target.image_dir.rglob("*.png")) == []


def test_full_clear_prunes_managed_orphans_only(repository, tmp_path):
    managed = repository.image_dir / ("a" * 64 + ".png")
    thumb_directory = repository.image_dir / "thumbnails"
    thumb_directory.mkdir()
    orphan_thumb = thumb_directory / ("b" * 64 + ".png")
    unrelated = repository.image_dir / "my-photo.png"
    nested = repository.image_dir / "user-folder"
    nested.mkdir()
    nested_managed_name = nested / ("c" * 64 + ".png")
    outside = tmp_path / ("d" * 64 + ".png")
    for file in (managed, orphan_thumb, unrelated, nested_managed_name, outside):
        file.write_bytes(b"content")
    assert repository.clear_history(include_protected=True) == 0
    assert not managed.exists()
    assert not orphan_thumb.exists()
    assert unrelated.exists() and nested_managed_name.exists() and outside.exists()


def test_cleanup_prunes_old_orphans_keeps_recent_and_referenced_protected(repository):
    image = QImage(10, 10, QImage.Format.Format_ARGB32)
    image.fill(QColor("green"))
    item = repository.add_image(image)
    repository.update(item["id"], favorite=True, pinned=True)
    stale = repository.image_dir / ("e" * 64 + ".png")
    stale_thumb = repository.image_dir / "thumbnails" / ("f" * 64 + ".png")
    recent = repository.image_dir / ("0" * 64 + ".png")
    unrelated = repository.image_dir / "keep-me.png"
    for file in (stale, stale_thumb, recent, unrelated):
        file.write_bytes(b"content")
    old = time.time() - 7200
    for file in (stale, stale_thumb, unrelated, Path(item["image_path"]), Path(item["thumbnail_path"])):
        os.utime(file, (old, old))
    assert repository.cleanup(max_items=0, max_age_days=0) == 0
    assert not stale.exists() and not stale_thumb.exists()
    assert recent.exists() and unrelated.exists()
    assert Path(item["image_path"]).exists() and Path(item["thumbnail_path"]).exists()
    assert repository.get(item["id"])["favorite"]
