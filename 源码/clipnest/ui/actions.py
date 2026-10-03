"""两种历史窗口共用的删除与整理操作。数据库工作始终在后台执行。"""
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QMessageBox, QCheckBox


class HistoryActions(QObject):
    changed = Signal()

    def __init__(self, repository, jobs, parent, notify):
        super().__init__(parent)
        self.repo, self.jobs, self.dialog_parent, self.notify = repository, jobs, parent, notify

    def _removed(self, count):
        self.notify(f"已删除 {count} 条记录")
        self.changed.emit()

    def delete_record(self, record):
        if not record:
            return
        if record.get("favorite") or record.get("pinned"):
            answer = QMessageBox.question(self.dialog_parent, "删除受保护记录", "这条记录已收藏或置顶。仍要删除它及关联图片吗？", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.notify("正在删除…")
        key = record["id"]
        self.jobs.submit(lambda: self.repo.delete_many([key]), self._removed, self.notify)

    def delete_selected(self, records):
        if not records:
            self.notify("请先选择要删除的记录")
            return
        protected = sum(bool(record.get("favorite") or record.get("pinned")) for record in records)
        text = f"删除选中的 {len(records)} 条记录及关联图片？此操作无法撤销。"
        if protected:
            text += f"\n其中 {protected} 条已收藏或置顶，默认保留。"
        dialog = QMessageBox(QMessageBox.Icon.Warning, "批量删除", text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, self.dialog_parent)
        include = QCheckBox("同时删除选中的收藏和置顶记录")
        if protected:
            dialog.setCheckBox(include)
        dialog.setDefaultButton(QMessageBox.StandardButton.No)
        if dialog.exec() != QMessageBox.StandardButton.Yes:
            return
        keys = list(dict.fromkeys(record["id"] for record in records if include.isChecked() or not (record.get("favorite") or record.get("pinned"))))
        if not keys:
            self.notify("收藏和置顶已保留，没有普通记录需要删除")
            return
        self.notify("正在删除选中记录…")
        include_protected = include.isChecked()
        self.jobs.submit(lambda: self.repo.delete_many(keys, include_protected=include_protected), self._removed, self.notify)

    def clear_all(self):
        dialog = QMessageBox(QMessageBox.Icon.Warning, "删除全部历史", "删除全部普通历史及关联图片？收藏和置顶默认保留。\n此操作无法撤销，包含当前筛选以外的记录。", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, self.dialog_parent)
        protected = QCheckBox("同时删除收藏和置顶记录")
        dialog.setCheckBox(protected)
        dialog.setDefaultButton(QMessageBox.StandardButton.No)
        if dialog.exec() != QMessageBox.StandardButton.Yes:
            return
        include_protected = protected.isChecked()
        self.notify("正在删除全部历史…")
        self.jobs.submit(lambda: self.repo.clear_history(include_protected), self._removed, self.notify)

    def toggle_record(self, record, field):
        if field not in ("favorite", "pinned") or not record:
            return
        key, value = record["id"], not bool(record[field])
        label = "收藏" if field == "favorite" else "置顶"
        self.jobs.submit(lambda: self.repo.update(key, **{field: value}), lambda _: (self.notify(("已" if value else "已取消") + label), self.changed.emit()), self.notify)
