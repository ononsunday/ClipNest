from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QCheckBox, QPushButton,
    QDialogButtonBox, QMessageBox)
from clipnest.core.text_tools import TOOLS, transform
from clipnest.ui.history import KINDS

from clipnest.core.models import LANGUAGES


class MetadataDialog(QDialog):
    def __init__(self, item, groups, parent=None):
        super().__init__(parent)
        self.setWindowTitle("整理记录")
        self.resize(460, 460)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.title_edit = QLineEdit(item.get("title", ""))
        self.notes = QPlainTextEdit(item.get("notes", ""))
        self.notes.setMaximumHeight(100)
        self.tags = QLineEdit(", ".join(item.get("tags", [])))
        self.tags.setPlaceholderText("多个标签用逗号分隔")
        self.group = QComboBox()
        self.group.setEditable(True)
        self.group.addItems([""] + groups)
        self.group.setCurrentText(item.get("group_name", ""))
        self.kind = QComboBox()
        for key, name in KINDS.items():
            if key != "image" or item["kind"] == "image":
                self.kind.addItem(name, key)
        self.kind.setCurrentIndex(max(0, self.kind.findData(item["kind"])))
        self.kind.setEnabled(item["kind"] != "image")
        self.language = QComboBox()
        self.language.addItems(LANGUAGES)
        self.language.setCurrentText(item.get("language", ""))
        form.addRow("名称", self.title_edit)
        form.addRow("备注", self.notes)
        form.addRow("标签", self.tags)
        form.addRow("收藏分组", self.group)
        form.addRow("内容分类", self.kind)
        form.addRow("代码语言", self.language)
        layout.addLayout(form)
        tip = QLabel("整理信息会保留原始内容。收藏分组与收藏状态可分别设置。")
        tip.setWordWrap(True)
        tip.setObjectName("muted")
        layout.addWidget(tip)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def fields(self):
        return {"title": self.title_edit.text().strip(), "notes": self.notes.toPlainText(),
            "tags": [x.strip() for x in self.tags.text().replace("，", ",").split(",") if x.strip()],
            "group_name": self.group.currentText().strip(), "kind": self.kind.currentData(),
            "language": self.language.currentText()}


class TextToolsDialog(QDialog):
    def __init__(self, text, jobs, copy, save, parent=None):
        super().__init__(parent)
        self.setWindowTitle("文本工具 · 先预览，再使用")
        self.resize(880, 610)
        self.jobs, self.copy, self.save = jobs, copy, save
        self.revision = 0
        self.alive = True
        self.finished.connect(lambda _: setattr(self, "alive", False))
        layout = QVBoxLayout(self)
        label = QLabel("原始记录保持原样。可在左侧输入任意文本，右侧查看处理结果。")
        label.setObjectName("muted")
        layout.addWidget(label)
        tools = QHBoxLayout()
        self.operation = QComboBox()
        for name, key in TOOLS.items():
            self.operation.addItem(name, key)
        tools.addWidget(self.operation)
        preview = QPushButton("生成预览")
        preview.setObjectName("primary")
        preview.clicked.connect(self.preview)
        tools.addWidget(preview)
        tools.addStretch()
        layout.addLayout(tools)
        columns = QHBoxLayout()
        self.input = QPlainTextEdit(text)
        self.input.setPlaceholderText("输入要处理的文本")
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlaceholderText("处理结果预览")
        columns.addWidget(self.input)
        columns.addWidget(self.output)
        layout.addLayout(columns, 1)
        self.status = QLabel("请选择工具后生成预览")
        self.status.setObjectName("muted")
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        self.copy_btn = QPushButton("复制处理结果")
        self.save_btn = QPushButton("保存为新记录")
        self.copy_btn.setEnabled(False)
        self.save_btn.setEnabled(False)
        self.copy_btn.clicked.connect(lambda: self.copy(self.output.toPlainText()))
        self.save_btn.clicked.connect(self.store)
        close = QPushButton("关闭")
        close.clicked.connect(self.reject)
        actions.addWidget(self.copy_btn)
        actions.addWidget(self.save_btn)
        actions.addStretch()
        actions.addWidget(close)
        layout.addLayout(actions)
        self.input.textChanged.connect(self.invalidate)
        self.operation.currentIndexChanged.connect(self.invalidate)

    def invalidate(self):
        self.revision += 1
        self.copy_btn.setEnabled(False)
        self.save_btn.setEnabled(False)
        self.status.setText("内容已变化，请重新生成预览")

    def preview(self):
        text, operation, revision = self.input.toPlainText(), self.operation.currentData(), self.revision
        self.status.setText("正在处理…")

        def done(result):
            if self.alive and revision == self.revision:
                self.output.setPlainText(result)
                self.copy_btn.setEnabled(True)
                self.save_btn.setEnabled(bool(result))
                self.status.setText(f"预览完成 · {len(result):,} 个字符")

        def failed(message):
            if self.alive:
                self.status.setText("无法处理：请检查 JSON 格式或输入内容。")
        self.jobs.submit(lambda: transform(text, operation), done, failed)

    def store(self):
        self.save(self.output.toPlainText())
        self.status.setText("已提交保存，主窗口会显示保存结果")


class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ClipNest 设置")
        self.resize(500, 560)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.theme = QComboBox()
        self.theme.addItem("浅色 · 淡蓝与白", "light")
        self.theme.addItem("深色 · 黑色", "dark")
        self.theme.setCurrentIndex(max(0, self.theme.findData(settings.get("theme", "light"))))
        self.hotkey = QLineEdit(settings.get("hotkey", "Ctrl+Alt+V"))
        self.max_items = QSpinBox()
        self.max_items.setRange(1, 100000)
        self.max_items.setValue(settings.get("max_items", 2000))
        self.days = QSpinBox()
        self.days.setRange(1, 3650)
        self.days.setValue(settings.get("max_age_days", 30))
        self.startup = QCheckBox("登录 Windows 时启动到托盘")
        self.startup.setChecked(settings.get("startup", False))
        self.close_after_paste = QCheckBox("粘贴后自动关闭快捷浮窗")
        self.close_after_paste.setChecked(settings.get("close_after_paste", False))
        self.excluded = QPlainTextEdit("\n".join(settings.get("excluded_apps", [])))
        self.excluded.setPlaceholderText("每行一个进程名，例如 KeePass.exe")
        self.excluded.setMaximumHeight(120)
        form.addRow("外观", self.theme)
        form.addRow("全局快捷键", self.hotkey)
        form.addRow("普通历史上限", self.max_items)
        form.addRow("普通历史保留天数", self.days)
        form.addRow("开机自启", self.startup)
        form.addRow("粘贴后", self.close_after_paste)
        form.addRow("不记录的应用", self.excluded)
        layout.addLayout(form)
        info = QLabel("内容仅保存在本机，不上传、不收集遥测。收藏和置顶不参与自动清理。\n排除应用按进程名判断，不能保证识别所有密码和验证码；复制敏感信息前可暂停记录。")
        info.setWordWrap(True)
        info.setObjectName("muted")
        layout.addWidget(info)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self):
        return {"theme": self.theme.currentData(), "hotkey": self.hotkey.text().strip(),
            "max_items": self.max_items.value(), "max_age_days": self.days.value(),
            "startup": self.startup.isChecked(), "close_after_paste": self.close_after_paste.isChecked(),
            "excluded_apps": [x.strip() for x in self.excluded.toPlainText().splitlines() if x.strip()]}
