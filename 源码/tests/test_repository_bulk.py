"""批量删除使用隔离数据库，验证事务和图片引用的完整性。"""
from pathlib import Path
import sqlite3

import pytest
from PySide6.QtGui import QImage, QColor

from clipnest.core.repository import Repository


@pytest.fixture
def repo(tmp_path):
    return Repository(tmp_path / "history.db", tmp_path / "images")


def test_delete_many_deduplicates_ids_and_updates_search_tags(repo):
    first = repo.add_text("批量删除 uniquealpha")
    second = repo.add_text("批量删除 uniquebeta")
    survivor = repo.add_text("留下 uniquegamma")
    repo.update(first["id"], favorite=True, pinned=True, tags=["已删除标签"])
    assert repo.delete_many([first["id"], second["id"], first["id"], "unknown", "'); DELETE FROM items;--"]) == 2
    assert repo.get(first["id"]) is None
    assert repo.get(second["id"]) is None
    assert repo.get(survivor["id"]) is not None
    assert repo.search("uniquealpha") == []
    assert repo.tags() == []


def test_empty_batch_keeps_unrelated_orphan_file(repo):
    orphan = repo.image_dir / ("a" * 64 + ".png")
    orphan.write_bytes(b"unreferenced task test file")
    assert repo.delete_many([]) == 0
    assert orphan.exists()


@pytest.mark.parametrize("invalid", [None, "single-id", b"id", [1], [""], ["id", None]])
def test_invalid_batch_is_rejected_before_deleting(repo, invalid):
    first = repo.add_text("keep if invalid")
    with pytest.raises(ValueError):
        repo.delete_many(invalid)
    assert repo.get(first["id"]) is not None


def make_image(repo):
    image = QImage(24, 18, QImage.Format.Format_ARGB32)
    image.fill(QColor("#245b90"))
    return repo.add_image(image)


def test_delete_many_removes_image_and_thumbnail(repo):
    image = make_image(repo)
    unrelated = repo.image_dir / ("b" * 64 + ".png")
    unrelated.write_bytes(b"unrelated orphan")
    paths = [Path(image["image_path"]), Path(image["thumbnail_path"])]
    assert all(path.exists() for path in paths)
    assert repo.delete_many([image["id"], image["id"]]) == 1
    assert all(not path.exists() for path in paths)
    assert unrelated.exists()


def test_delete_many_preserves_image_paths_referenced_by_survivor(repo):
    image = make_image(repo)
    survivor = repo.add_text("shared reference test")
    with repo._connection() as connection:
        connection.execute("UPDATE items SET image_path=?,thumbnail_path=? WHERE id=?",
                           (image["image_path"], image["thumbnail_path"], survivor["id"]))
    assert repo.delete_many([image["id"]]) == 1
    assert Path(image["image_path"]).exists()
    assert Path(image["thumbnail_path"]).exists()
    assert repo.delete_many([survivor["id"]]) == 1
    assert not Path(image["image_path"]).exists()
    assert not Path(image["thumbnail_path"]).exists()


def test_delete_many_never_removes_external_path(repo, tmp_path):
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"not managed by ClipNest")
    item = repo.add_text("external reference")
    with repo._connection() as connection:
        connection.execute("UPDATE items SET image_path=? WHERE id=?", (str(outside), item["id"]))
    assert repo.delete_many([item["id"]]) == 1
    assert outside.read_bytes() == b"not managed by ClipNest"


def test_delete_many_rolls_back_all_chunks_and_leaves_images_on_failure(repo):
    image = make_image(repo)
    ids = [image["id"]]
    with repo._connection() as connection:
        for number in range(800):
            identity = f"bulk-transaction-{number}"
            ids.append(identity)
            connection.execute(
                "INSERT INTO items(id,content_hash,text,kind,created_at,last_copied_at) VALUES (?,?,?,'text',?,?)",
                (identity, f"hash-{number}", f"rollbackneedle{number}", "2026-10-03T00:00:00+00:00", "2026-10-03T00:00:00+00:00"),
            )
        connection.execute("CREATE TRIGGER abort_last_delete BEFORE DELETE ON items WHEN OLD.id='bulk-transaction-799' BEGIN SELECT RAISE(ABORT,'test failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        repo.delete_many(ids)
    assert repo.stats()["total"] == 801
    assert len(repo.search("rollbackneedle0")) == 1
    assert Path(image["image_path"]).exists()
    assert Path(image["thumbnail_path"]).exists()

