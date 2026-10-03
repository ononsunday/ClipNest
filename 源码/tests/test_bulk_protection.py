"""收藏状态比界面刷新更早提交时，批量删除也必须保留它。"""
from clipnest.core.repository import Repository


def test_bulk_delete_protects_favorite_and_pin_at_database_commit(tmp_path):
    repo = Repository(tmp_path / "history.db", tmp_path / "images")
    ordinary = repo.add_text("ordinary")
    favorite = repo.add_text("favorite after selection")
    pinned = repo.add_text("pinned after selection")
    selected_ids = [ordinary["id"], favorite["id"], pinned["id"]]
    repo.update(favorite["id"], favorite=True)
    repo.update(pinned["id"], pinned=True)
    assert repo.delete_many(selected_ids, include_protected=False) == 1
    assert repo.get(favorite["id"])["favorite"]
    assert repo.get(pinned["id"])["pinned"]
    assert repo.delete_many(selected_ids, include_protected=True) == 2
