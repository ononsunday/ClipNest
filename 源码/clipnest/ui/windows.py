from datetime import datetime, timedelta, timezone
from pathlib import Path
from html import escape
from PySide6.QtCore import Qt, QTimer, QEvent, Signal, QPoint
from PySide6.QtGui import QPixmap, QImage, QFont, QShortcut, QKeySequence, QCursor, QColor
from PySide6.QtWidgets import (QApplication, QWidget, QMainWindow, QDialog, QVBoxLayout,
    QHBoxLayout, QLabel, QPushButton, QLineEdit, QComboBox, QListWidget, QListWidgetItem,
    QSplitter, QTextBrowser, QStackedWidget, QMessageBox, QFileDialog, QInputDialog,
    QCheckBox, QGraphicsDropShadowEffect, QPlainTextEdit, QDialogButtonBox, QMenu)
from pygments import highlight
from pygments.lexers import get_lexer_by_name
from pygments.formatters import HtmlFormatter
from clipnest.ui.history import HistoryList, KINDS
from clipnest.ui.dialogs import MetadataDialog, TextToolsDialog, SettingsDialog, LANGUAGES
from clipnest.ui.theme import apply_theme
from clipnest.ui.widgets import PassiveComboBox
from clipnest.ui.actions import HistoryActions
from clipnest.ui.paste import PasteController
from clipnest.platform import windows as win_api
from clipnest.platform.windows import bring_to_front, set_startup


def button(text, slot, primary=False):
    result = QPushButton(text)
    result.clicked.connect(slot)
    if primary:
        result.setObjectName("primary")
    return result


def code_html(item, dark):
    text = item.get("text", "")[:60000]
    try:
        language = {"C++": "cpp", "C": "c", "JavaScript": "javascript"}.get(item.get("language"), (item.get("language") or "text").lower())
        lexer = get_lexer_by_name(language, stripnl=False, ensurenl=False)
    except Exception:
        lexer = get_lexer_by_name("text", stripnl=False, ensurenl=False)
    formatter = HtmlFormatter(noclasses=True, style="monokai" if dark else "friendly")
    return highlight(text, lexer, formatter)


class SearchPane(QWidget):
    chosen = Signal(object)
    copy_requested = Signal(object)
    delete_requested = Signal(object)
    delete_many_requested = Signal(object)
    clear_requested = Signal()
    toggle_requested = Signal(object, str)
    menu_opened = Signal()
    menu_closed = Signal()

    def __init__(self, repository, jobs, parent=None, compact=False):
        super().__init__(parent)
        self.repo, self.jobs, self.compact = repository, jobs, compact
        self.scope = {}
        self.revision = 0
        self.offset = 0
        self.busy = False
        self.requested_id = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索复制过的内容、名称、备注或标签…")
        self.search.setMinimumHeight(43)
        layout.addWidget(self.search)
        filters = QHBoxLayout()
        self.kind = PassiveComboBox(self) if compact else QComboBox()
        self.kind.addItem("全部类型", "")
        for key, label in KINDS.items():
            self.kind.addItem(label, key)
        self.time = PassiveComboBox(self) if compact else QComboBox()
        for label, days in [("全部时间", 0), ("今天", 1), ("近 7 天", 7), ("近 30 天", 30)]:
            self.time.addItem(label, days)
        self.favorite = QCheckBox("收藏")
        self.pinned = QCheckBox("置顶")
        filters.addWidget(self.kind)
        filters.addWidget(self.time)
        filters.addWidget(self.favorite)
        filters.addWidget(self.pinned)
        layout.addLayout(filters)
        toolbar = QHBoxLayout()
        self.bulk_button = button("批量选择", lambda: None)
        self.bulk_button.setCheckable(True)
        self.bulk_button.toggled.connect(self.set_bulk_mode)
        self.select_all_button = button("全选", lambda: self.list.selectAll())
        self.select_all_button.setToolTip("选择当前已加载的记录；加载更多后可以继续全选")
        self.delete_selected_button = button("删除选中（0）", lambda: self.delete_many_requested.emit(self.list.selected_records()))
        self.delete_selected_button.setObjectName("danger")
        self.clear_button = button("删除全部", self.clear_requested.emit)
        self.clear_button.setObjectName("danger")
        for control in [self.bulk_button, self.select_all_button, self.delete_selected_button, self.clear_button]:
            toolbar.addWidget(control)
        toolbar.addStretch()
        self.select_all_button.hide()
        self.delete_selected_button.hide()
        layout.addLayout(toolbar)
        self.list = HistoryList()
        self.list.setProperty("singleAction", compact)
        self.list.currentItemChanged.connect(lambda current, _: self.chosen.emit(current.data(Qt.ItemDataRole.UserRole) if current else None))
        self.list.itemSelectionChanged.connect(self.update_bulk_state)
        self.list.delete_requested.connect(self.delete_requested.emit)
        if compact:
            self.list.activate_requested.connect(self.copy_requested.emit)
        else:
            self.list.itemDoubleClicked.connect(lambda item: self.copy_requested.emit(item.data(Qt.ItemDataRole.UserRole)) if not self.bulk_button.isChecked() else None)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self.context_menu)
        layout.addWidget(self.list, 1)
        footer = QHBoxLayout()
        self.count = QLabel("正在读取…")
        self.count.setObjectName("muted")
        self.more = button("加载更多", self.load_more)
        self.more.setVisible(False)
        footer.addWidget(self.count)
        footer.addStretch()
        footer.addWidget(self.more)
        layout.addLayout(footer)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self.refresh)
        # 输入变化即使旧搜索失效，避免旧结果覆盖新搜索。
        self.search.textChanged.connect(self.schedule)
        for control in [self.kind, self.time]:
            control.currentIndexChanged.connect(self.schedule)
        for control in [self.favorite, self.pinned]:
            control.toggled.connect(self.schedule)
        self.search.installEventFilter(self)
        self.list.installEventFilter(self)

    def schedule(self, *_):
        self.revision += 1
        self.timer.start()

    def parameters(self):
        parameters = dict(self.scope)
        parameters["query"] = self.search.text()
        if self.kind.currentData():
            parameters["kind"] = self.kind.currentData()
        if self.favorite.isChecked():
            parameters["favorite"] = True
        if self.pinned.isChecked():
            parameters["pinned"] = True
        days = self.time.currentData()
        if days:
            now = datetime.now(timezone.utc)
            start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc) if days == 1 else now - timedelta(days=days)
            parameters["since"] = start.isoformat()
        return parameters

    def refresh(self, *_):
        self.timer.stop()
        self.offset = 0
        self.fetch(False)

    def load_more(self):
        if not self.busy:
            self.offset = self.list.count()
            self.fetch(True)

    def fetch(self, append):
        self.revision += 1
        token = self.revision
        params = self.parameters()
        params.update(limit=100, offset=self.offset)
        current = self.list.currentItem()
        previous = self.requested_id or (current.data(Qt.ItemDataRole.UserRole)["id"] if current else None)
        selected_ids = {record["id"] for record in self.list.selected_records()} if self.bulk_button.isChecked() else set()
        self.busy = True
        self.more.setEnabled(False)
        self.count.setText("正在搜索…")

        def done(items):
            if token != self.revision:
                return
            self.busy = False
            self.more.setEnabled(True)
            self.list.blockSignals(True)
            if not append:
                self.list.clear()
            selected = 0
            for record in items:
                row = QListWidgetItem()
                row.setData(Qt.ItemDataRole.UserRole, record)
                self.list.addItem(row)
                if record["id"] == previous:
                    selected = self.list.count() - 1
                if record["id"] in selected_ids:
                    row.setSelected(True)
            self.list.blockSignals(False)
            self.more.setVisible(len(items) == 100)
            self.count.setText(f"已显示 {self.list.count()} 条" if self.list.count() else "还没有记录 · 复制内容后会显示在这里")
            if not append:
                if not self.bulk_button.isChecked():
                    self.list.setCurrentRow(selected)
                self.requested_id = None
                if not items:
                    self.chosen.emit(None)
            self.update_bulk_state()

        def fail(message):
            if token == self.revision:
                self.busy = False
                self.more.setEnabled(True)
                self.count.setText(message)
        self.jobs.submit(lambda: self.repo.search(**params), done, fail)

    def set_bulk_mode(self, enabled):
        self.list.set_bulk_mode(enabled)
        self.select_all_button.setVisible(enabled)
        self.delete_selected_button.setVisible(enabled)
        self.bulk_button.setText("完成选择" if enabled else "批量选择")
        self.update_bulk_state()

    def update_bulk_state(self):
        count = len(self.list.selected_records()) if self.bulk_button.isChecked() else 0
        self.delete_selected_button.setText(f"删除选中（{count}）")
        self.delete_selected_button.setEnabled(count > 0)

    def context_menu(self, position):
        row = self.list.itemAt(position)
        record = row.data(Qt.ItemDataRole.UserRole) if row else None
        self.menu_opened.emit()
        menu = QMenu(self)
        self.context = menu
        if record:
            action = menu.addAction("粘贴到原输入框" if self.compact else "复制")
            action.setEnabled(not self.bulk_button.isChecked())
            action.triggered.connect(lambda: self.copy_requested.emit(record))
            if self.compact:
                menu.addAction("仅复制", lambda: self.parent().copy_only(record))
            menu.addSeparator()
            menu.addAction("取消置顶" if record["pinned"] else "置顶", lambda: self.toggle_requested.emit(record, "pinned"))
            menu.addAction("取消收藏" if record["favorite"] else "收藏", lambda: self.toggle_requested.emit(record, "favorite"))
            menu.addSeparator()
            menu.addAction("删除", lambda: self.delete_requested.emit(record))
        menu.addSeparator()
        menu.addAction("完成批量选择" if self.bulk_button.isChecked() else "批量选择", lambda: self.bulk_button.setChecked(not self.bulk_button.isChecked()))
        if self.bulk_button.isChecked():
            selected = menu.addAction("删除选中记录", lambda: self.delete_many_requested.emit(self.list.selected_records()))
            selected.setEnabled(bool(self.list.selected_records()))
        menu.addAction("删除全部历史…", self.clear_requested.emit)
        menu.aboutToHide.connect(self.menu_closed.emit)
        menu.popup(self.list.viewport().mapToGlobal(position))

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up) and watched == self.search:
                row = self.list.currentRow()
                row += 1 if key == Qt.Key.Key_Down else -1
                self.list.setCurrentRow(max(0, min(row, self.list.count() - 1)))
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if event.isAutoRepeat():
                    return True
                item = self.list.currentItem()
                if item and not self.bulk_button.isChecked():
                    self.copy_requested.emit(item.data(Qt.ItemDataRole.UserRole))
                return True
            if key == Qt.Key.Key_Delete and watched == self.list:
                if self.bulk_button.isChecked():
                    self.delete_many_requested.emit(self.list.selected_records())
                elif self.list.currentItem():
                    self.delete_requested.emit(self.list.currentItem().data(Qt.ItemDataRole.UserRole))
                return True
        return super().eventFilter(watched, event)


class QuickWindow(QDialog):
    def __init__(self, repo, jobs, copy, parent=None, settings=None, paste=None):
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.settings = settings
        self.passive = True
        self._target_window = 0
        self._last_position = None
        self._drag_offset = None
        self._drag_moved = False
        self._copy = copy
        self._paste_busy = False
        self._operation = 0
        self.paste_controller = PasteController(self, paste)
        self._copy_timer = QTimer(self)
        self._copy_timer.setSingleShot(True)
        self._copy_timer.setInterval(10000)
        self._copy_timer.timeout.connect(lambda: self.copy_failed("复制未完成，请稍后重试"))
        self.setObjectName("quickWindow")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("ClipNest 快捷搜索")
        self.resize(600, 620)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        self.card = QWidget()
        self.card.setObjectName("quickCard")
        shadow = QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(22)
        shadow.setOffset(0, 3)
        shadow.setColor(QColor(0, 0, 0, 65))
        self.card.setGraphicsEffect(shadow)
        outer.addWidget(self.card)
        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(18, 18, 18, 16)
        self.drag_header = QWidget()
        self.drag_header.setObjectName("dragHeader")
        self.drag_header.setCursor(Qt.CursorShape.OpenHandCursor)
        self.drag_header.setToolTip("按住这里拖动窗口，松开后保存位置")
        header = QHBoxLayout(self.drag_header)
        header.setContentsMargins(0, 0, 0, 0)
        title = QLabel("ClipNest")
        self.brand_label = title
        title.setObjectName("brand")
        title.setCursor(Qt.CursorShape.OpenHandCursor)
        title.setToolTip("按住标题拖动窗口")
        header.addWidget(title)
        header.addStretch()
        self.close_button = button("关闭", self.hide)
        header.addWidget(self.close_button)
        layout.addWidget(self.drag_header)
        self.pane = SearchPane(repo, jobs, self, compact=True)
        self.pane.copy_requested.connect(lambda item: self.choose(item, copy))
        layout.addWidget(self.pane, 1)
        self.hint = QLabel("单击记录粘贴 · 右键整理 · 按住顶部标题拖动")
        self.hint.setObjectName("muted")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.pane.search.installEventFilter(self)
        self._focus_controls = [self.pane.search, self.pane.list, self.pane.kind, self.pane.time,
            self.pane.favorite, self.pane.pinned, self.pane.more, self.close_button,
            self.pane.bulk_button, self.pane.select_all_button, self.pane.delete_selected_button, self.pane.clear_button]
        self._focus_policies = {control: control.focusPolicy() for control in self._focus_controls}
        # 下拉菜单也使用不激活显示，不能在被动模式中抢走原应用焦点。
        self._combo_popups = [self.pane.kind.view().window(), self.pane.time.view().window()]
        for popup in self._combo_popups:
            popup.installEventFilter(self)
            popup.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        # 子控件与弹出菜单创建完毕后再监听，避免构造时的 ChildAdded 访问未初始化字段。
        for control in self._focus_controls:
            control.installEventFilter(self)
        self.pane.list.viewport().installEventFilter(self)
        self.drag_header.installEventFilter(self)
        self.brand_label.installEventFilter(self)
        self.actions = HistoryActions(repo, jobs, self, self.hint.setText)
        self.actions.changed.connect(self.cancel_pending)
        self.actions.changed.connect(self.pane.refresh)
        self.pane.delete_requested.connect(self.actions.delete_record)
        self.pane.delete_many_requested.connect(self.actions.delete_selected)
        self.pane.clear_requested.connect(self.actions.clear_all)
        self.pane.toggle_requested.connect(self.actions.toggle_record)
        self.pane.menu_opened.connect(self.remember_target)
        self.pane.menu_closed.connect(lambda: QTimer.singleShot(0, self.return_to_target))
        QShortcut(QKeySequence("Esc"), self, activated=self.hide)
        self._set_passive(True)

    def choose(self, item, copy):
        if not item or self.pane.bulk_button.isChecked() or self._paste_busy:
            return
        self.remember_target()
        target = self._target_window
        self._paste_busy = True
        self._operation += 1
        operation = self._operation
        self.hint.setText("正在复制并准备粘贴…")
        self._copy_timer.start()
        copy(item, lambda: self._copied(target, operation))

    def own_handles(self):
        return {int(widget.winId()) for widget in QApplication.topLevelWidgets() if widget.windowHandle() is not None}

    def remember_target(self):
        current = win_api.foreground_window()
        owned = {int(widget.winId()): widget for widget in QApplication.topLevelWidgets() if widget.windowHandle() is not None}
        if current and current not in owned:
            self._target_window = current
        elif not current or not (owned[current] is self or self.isAncestorOf(owned[current])):
            # 从管理窗口呼出时，不把过往外部窗口当作“当前输入框”。
            self._target_window = 0

    def _copied(self, target, operation):
        if operation != self._operation:
            return
        self._copy_timer.stop()
        self._set_passive(True)
        self.paste_controller.start(target, self.own_handles(), lambda ok, message: self._pasted(ok, message, operation))

    def _pasted(self, success, message, operation):
        if operation != self._operation:
            return
        self._paste_busy = False
        self.hint.setText(message)
        if success and self.settings and self.settings.get("close_after_paste", False):
            self.hide()

    def cancel_pending(self):
        self._operation += 1
        self._paste_busy = False
        self._copy_timer.stop()
        self.paste_controller.cancel()

    def copy_failed(self, message):
        self.cancel_pending()
        self.hint.setText(message)

    def return_to_target(self):
        current = win_api.foreground_window()
        self._set_passive(True)
        if current in self.own_handles() and win_api.window_is_valid(self._target_window):
            win_api.restore_foreground(self._target_window)

    def copy_only(self, item):
        if self._paste_busy:
            return
        self._copy(item, lambda: (self.return_to_target(), self.hint.setText("已复制，可在目标软件按 Ctrl+V")))

    def _set_passive(self, passive):
        self.passive = passive
        for control in self._focus_controls:
            control.setFocusPolicy(Qt.FocusPolicy.NoFocus if passive else self._focus_policies[control])
        if passive:
            self.pane.search.clearFocus()
        win_api.set_window_no_activate(self, passive)
        for popup in self._combo_popups:
            win_api.set_window_no_activate(popup, passive)

    def activate_search(self):
        if not self.isVisible():
            return
        for popup in self._combo_popups:
            popup.hide()
        self.remember_target()
        self._set_passive(False)
        bring_to_front(self)
        self.pane.search.setFocus(Qt.FocusReason.MouseFocusReason)
        self.hint.setText("↑ ↓ 选择    Enter 粘贴    Esc 关闭 · 按住标题拖动")

    def _position(self):
        saved = self.settings.get("quick_position") if self.settings else None
        return QPoint(*saved) if saved else self._last_position

    def _screen_for_position(self, position):
        if position is not None:
            # 检查标题所在的屏幕，屏幕移除后回到当前鼠标所在的可见屏幕。
            for screen in QApplication.screens():
                if screen.availableGeometry().contains(position + QPoint(40, 30)):
                    return screen, True
        return QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen(), False

    def _bounded(self, position, screen):
        area = screen.availableGeometry()
        return QPoint(max(area.left(), min(position.x(), area.right() - self.width() + 1)),
            max(area.top(), min(position.y(), area.bottom() - self.height() + 1)))

    def _save_position(self):
        screen, valid = self._screen_for_position(self.pos())
        if not valid:
            screen = self.screen() or screen
        area = screen.availableGeometry()
        self.resize(min(self.width(), max(240, area.width() - 24)), min(self.height(), max(300, area.height() - 24)))
        self.move(self._bounded(self.pos(), screen))
        self._last_position = QPoint(self.pos())
        if self.settings:
            try:
                self.settings.set("quick_position", [self.x(), self.y()])
                self.settings.save()
            except (OSError, ValueError):
                self.hint.setText("位置已移动，但无法保存；请检查设置目录权限")

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.MouseButtonPress:
            for popup in self._combo_popups:
                if getattr(popup, "combo", None) != watched:
                    popup.hide()
        if watched == self.pane.search and event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick) and event.button() == Qt.MouseButton.LeftButton:
            self.activate_search()
        elif watched in self._combo_popups and event.type() == QEvent.Type.Show:
            win_api.set_window_no_activate(watched, self.passive)
        elif watched in (self.drag_header, self.brand_label):
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                self._drag_moved = False
                self.drag_header.setCursor(Qt.CursorShape.ClosedHandCursor)
                self.drag_header.grabMouse()
                return True
            if event.type() == QEvent.Type.MouseMove and self._drag_offset is not None:
                self.move(event.globalPosition().toPoint() - self._drag_offset)
                self._drag_moved = True
                return True
            if event.type() == QEvent.Type.MouseButtonRelease and self._drag_offset is not None:
                self._drag_offset = None
                self.drag_header.releaseMouse()
                self.drag_header.setCursor(Qt.CursorShape.OpenHandCursor)
                if self._drag_moved:
                    self._save_position()
                return True
        return super().eventFilter(watched, event)

    def nativeEvent(self, event_type, message):
        if win_api.IS_WINDOWS and self.passive and bytes(event_type) in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x0021:  # WM_MOUSEACTIVATE
                return True, 3  # MA_NOACTIVATE：处理点击，但保留前台应用的输入焦点。
        return super().nativeEvent(event_type, message)

    def event(self, event):
        if event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and (self.passive or self._paste_busy):
            event.accept()
            return True
        if event.type() == QEvent.Type.WindowDeactivate and hasattr(self, "_focus_controls"):
            # 允许正在操作的下拉菜单完成；点击其他软件时回到被动悬浮状态。
            QTimer.singleShot(0, self._deactivated)
        return super().event(event)

    def _deactivated(self):
        if self.isVisible() and not self.isActiveWindow() and not QApplication.activePopupWidget():
            self._set_passive(True)

    def hideEvent(self, event):
        self.cancel_pending()
        for control in (self.pane.kind, self.pane.time):
            control.hidePopup()
        if self._drag_offset is not None:
            self._drag_offset = None
            self.drag_header.releaseMouse()
            self.drag_header.setCursor(Qt.CursorShape.OpenHandCursor)
        super().hideEvent(event)

    def summon(self):
        self.remember_target()
        self._set_passive(True)
        position = self._position()
        screen, valid = self._screen_for_position(position)
        area = screen.availableGeometry()
        self.resize(min(600, area.width() - 40), min(620, area.height() - 40))
        self.move(self._bounded(position, screen) if valid else area.center() - self.rect().center())
        win_api.show_no_activate_topmost(self)
        self.hint.setText("单击记录粘贴 · 右键整理 · 按住顶部标题拖动")
        self.pane.refresh()


class MainWindow(QMainWindow):
    def __init__(self, repo, settings, jobs, monitor, hotkey, app):
        super().__init__()
        self.repo, self.settings, self.jobs, self.monitor, self.hotkey, self.app = repo, settings, jobs, monitor, hotkey, app
        self.item = None
        self.detail_token = 0
        self.exiting = False
        self.setWindowTitle("ClipNest · 智能剪贴板")
        self.resize(1230, 780)
        self.setMinimumSize(920, 610)
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 18, 0)
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(190)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(16, 25, 16, 18)
        brand = QLabel("ClipNest")
        brand.setObjectName("brand")
        side.addWidget(brand)
        tag = QLabel("找回每一次复制")
        tag.setObjectName("muted")
        side.addWidget(tag)
        side.addSpacing(25)
        self.nav = QListWidget()
        self.nav.setObjectName("navigation")
        self.nav.addItems(["最近记录", "全部历史", "收藏夹", "置顶记录", "代码片段", "文本工具", "设置"])
        self.nav.currentRowChanged.connect(self.navigate)
        side.addWidget(self.nav, 1)
        self.group = QComboBox()
        self.group.addItem("收藏分组", "")
        self.group.currentIndexChanged.connect(self.filter_group)
        side.addWidget(self.group)
        side.addWidget(button("＋ 新建分组", self.create_group))
        self.tags = QComboBox()
        self.tags.addItem("全部标签", "")
        self.tags.currentIndexChanged.connect(self.filter_tag)
        side.addWidget(self.tags)
        side.addSpacing(12)
        self.pause = button("暂停记录", self.toggle_pause)
        side.addWidget(self.pause)
        self.local = QLabel("数据仅保存在本机")
        self.local.setObjectName("muted")
        side.addWidget(self.local)
        root.addWidget(sidebar)
        content = QVBoxLayout()
        content.setContentsMargins(18, 25, 0, 18)
        header = QHBoxLayout()
        self.heading = QLabel("最近记录")
        self.heading.setObjectName("heading")
        header.addWidget(self.heading)
        header.addStretch()
        self.quick_btn = button(settings.get("hotkey", "Ctrl+Alt+V"), lambda: self.quick.summon())
        header.addWidget(self.quick_btn)
        content.addLayout(header)
        caption = QLabel("搜索、整理，再次使用。双击记录即可复制。")
        caption.setObjectName("muted")
        content.addWidget(caption)
        content.addSpacing(10)
        splitter = QSplitter()
        self.pane = SearchPane(repo, jobs)
        self.pane.setMinimumWidth(335)
        self.pane.chosen.connect(self.show_item)
        self.pane.copy_requested.connect(self.copy_item)
        splitter.addWidget(self.pane)
        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(12, 0, 0, 0)
        self.title = QLabel("选择一条记录")
        self.title.setObjectName("detailTitle")
        self.title.setWordWrap(True)
        detail_layout.addWidget(self.title)
        self.meta = QLabel("完整内容将在这里显示")
        self.meta.setObjectName("muted")
        self.meta.setWordWrap(True)
        detail_layout.addWidget(self.meta)
        self.language = QComboBox()
        self.language.addItems(LANGUAGES)
        self.language.currentTextChanged.connect(self.change_language)
        detail_layout.addWidget(self.language)
        self.stack = QStackedWidget()
        self.text = QTextBrowser()
        self.text.setOpenExternalLinks(False)
        self.text.setOpenLinks(False)
        self.text.setFont(QFont("Consolas", 11))
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setMinimumSize(200, 200)
        self.stack.addWidget(self.text)
        self.stack.addWidget(self.image)
        detail_layout.addWidget(self.stack, 1)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setObjectName("muted")
        detail_layout.addWidget(self.note)
        actions = QHBoxLayout()
        self.copy_btn = button("复制原始内容", lambda: self.copy_item(self.item), True)
        self.fav_btn = button("收藏", lambda: self.toggle("favorite"))
        self.pin_btn = button("置顶", lambda: self.toggle("pinned"))
        actions.addWidget(self.copy_btn)
        actions.addWidget(self.fav_btn)
        actions.addWidget(self.pin_btn)
        detail_layout.addLayout(actions)
        actions2 = QHBoxLayout()
        self.edit_btn = button("整理信息", self.edit_metadata)
        self.edit_content_btn = button("编辑原文", self.edit_content)
        self.tools_btn = button("文本处理", self.tools)
        self.delete_btn = button("删除", self.delete_item)
        self.delete_btn.setObjectName("danger")
        for btn in [self.edit_btn, self.edit_content_btn, self.tools_btn, self.delete_btn]:
            actions2.addWidget(btn)
        detail_layout.addLayout(actions2)
        splitter.addWidget(detail)
        splitter.setSizes([400, 470])
        content.addWidget(splitter, 1)
        bottom = QHBoxLayout()
        self.summary = QLabel()
        self.summary.setObjectName("muted")
        bottom.addWidget(self.summary)
        bottom.addStretch()
        bottom.addWidget(button("导入", self.import_data))
        bottom.addWidget(button("导出", self.export_data))
        bottom.addWidget(button("清空历史", self.clear_history))
        content.addLayout(bottom)
        root.addLayout(content, 1)
        self.quick = QuickWindow(repo, jobs, self.copy_item, settings=settings)
        self.actions = HistoryActions(repo, jobs, self, self.notice)
        self.actions.changed.connect(self.refresh)
        self.quick.actions.changed.connect(self.refresh)
        self.pane.delete_requested.connect(self.actions.delete_record)
        self.pane.delete_many_requested.connect(self.actions.delete_selected)
        self.pane.clear_requested.connect(self.actions.clear_all)
        self.pane.toggle_requested.connect(self.actions.toggle_record)
        hotkey.triggered.connect(self.quick.summon)
        self.nav.setCurrentRow(0)
        self.show_item(None)
        self.update_pause()
        self.refresh()

    def notice(self, text):
        self.statusBar().showMessage(text, 9000)
        if self.quick.isVisible():
            if self.quick._paste_busy and text.startswith(("操作失败", "复制失败")):
                self.quick.copy_failed(text)
            else:
                self.quick.hint.setText(text)

    def refresh(self, *_):
        self.pane.refresh()
        if self.quick.isVisible():
            self.quick.pane.refresh()
        self.jobs.submit(lambda: (self.repo.stats(), self.repo.groups(), self.repo.tags()), self.library_info, self.notice)

    def library_info(self, result):
        stats, groups, tags = result
        self.summary.setText(f"{stats['total']:,} 条记录 · {stats['favorites']:,} 条收藏")
        for control, label, values in [(self.group, "收藏分组", groups), (self.tags, "全部标签", tags)]:
            selected = control.currentData()
            control.blockSignals(True)
            control.clear()
            control.addItem(label, "")
            for value in values:
                control.addItem(value, value)
            control.setCurrentIndex(max(0, control.findData(selected)))
            control.blockSignals(False)

    def navigate(self, row):
        if row == 5:
            self.tools()
            return
        if row == 6:
            self.open_settings()
            return
        self.heading.setText(self.nav.item(row).text())
        self.pane.scope = {0: {"limit": 100}, 1: {}, 2: {"favorite": True}, 3: {"pinned": True}, 4: {"kind": "code"}}.get(row, {})
        self.filter_group(refresh=False)
        self.filter_tag(refresh=False)
        self.pane.refresh()

    def filter_group(self, *_args, refresh=True):
        if self.group.currentData():
            self.pane.scope["group"] = self.group.currentData()
        else:
            self.pane.scope.pop("group", None)
        if refresh:
            self.pane.refresh()

    def filter_tag(self, *_args, refresh=True):
        if self.tags.currentData():
            self.pane.scope["tag"] = self.tags.currentData()
        else:
            self.pane.scope.pop("tag", None)
        if refresh:
            self.pane.refresh()

    def show_item(self, item):
        self.item = item
        self.detail_token += 1
        token = self.detail_token
        for control in [self.copy_btn, self.fav_btn, self.pin_btn, self.edit_btn, self.delete_btn, self.language]:
            control.setEnabled(bool(item))
        self.tools_btn.setEnabled(not item or item["kind"] != "image")
        self.edit_content_btn.setEnabled(bool(item and item["kind"] != "image"))
        self.language.blockSignals(True)
        self.language.setCurrentText(item.get("language", "") if item else "")
        self.language.blockSignals(False)
        self.language.setVisible(bool(item and item["kind"] == "code"))
        if not item:
            self.title.setText("选择一条记录")
            self.meta.setText("复制内容后，即可在这里搜索和整理")
            self.text.setPlainText("")
            self.stack.setCurrentIndex(0)
            self.note.clear()
            return
        self.title.setText(item.get("title") or KINDS.get(item["kind"], "记录"))
        self.meta.setText(f"{KINDS.get(item['kind'], item['kind'])} · {item['last_copied_at'][:19].replace('T', ' ')} UTC")
        self.fav_btn.setText("取消收藏" if item["favorite"] else "收藏")
        self.pin_btn.setText("取消置顶" if item["pinned"] else "置顶")
        self.note.setText(item.get("notes", "")[:1000] + ("\n标签：" + "、".join(item["tags"]) if item.get("tags") else ""))
        if item["kind"] == "image":
            self.stack.setCurrentIndex(1)
            self.image.setText("正在读取图片…")
            self.jobs.submit(lambda: QImage(item.get("thumbnail_path") or item["image_path"]),
                lambda img: self.preview_image(img, token), self.notice)
        else:
            self.stack.setCurrentIndex(0)
            if item["kind"] == "code" or item.get("language"):
                self.text.setStyleSheet("font-family:Consolas;")
                self.text.setPlainText("正在生成代码预览…")
                dark = self.settings.get("theme") == "dark"
                self.jobs.submit(lambda: code_html(item, dark),
                    lambda html: self.text.setHtml(html) if token == self.detail_token else None, self.notice)
            else:
                self.text.setStyleSheet("font-family:'Microsoft YaHei UI';")
                self.text.setPlainText(item.get("text", "")[:60000])
            if len(item.get("text", "")) > 60000:
                self.note.setText(self.note.text() + "\n预览显示前 60,000 字符；复制会使用完整原文。")

    def preview_image(self, img, token):
        if token != self.detail_token:
            return
        if img.isNull():
            self.image.setText("图片文件不可用")
        else:
            self.image.setPixmap(QPixmap.fromImage(img).scaled(self.image.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def copy_item(self, item, done=None):
        if not item:
            return
        # 后台加载完整记录和原图，剪贴板写入保持在主线程。
        def read():
            fresh = self.repo.get(item["id"])
            if not fresh:
                raise FileNotFoundError()
            return fresh, QImage(fresh["image_path"]) if fresh["kind"] == "image" else None

        def copy(result):
            fresh, image = result
            try:
                if image is not None:
                    if image.isNull():
                        raise FileNotFoundError()
                    success = self.monitor.copy_image(image)
                else:
                    success = self.monitor.copy_text(fresh["text"])
                if not success:
                    raise RuntimeError("clipboard unavailable")
                self.notice("已复制到系统剪贴板")
                self.jobs.submit(lambda: self.repo.update(fresh["id"], last_copied_at=datetime.now(timezone.utc).isoformat()), lambda _: self.refresh(), self.notice)
                if done:
                    done()
            except Exception:
                self.notice("复制失败，请稍后重试或检查图片文件是否存在")
        self.jobs.submit(read, copy, self.notice)

    def copy_text(self, text):
        try:
            if not self.monitor.copy_text(text):
                raise RuntimeError("clipboard unavailable")
            self.notice("已复制处理结果")
        except Exception:
            self.notice("剪贴板暂不可用，请重试")

    def save_text(self, text):
        if text:
            self.jobs.submit(lambda: self.repo.add_text(text, source="ClipNest 文本工具"),
                lambda _: (self.notice("处理结果已保存为记录"), self.refresh()), self.notice)

    def toggle(self, field):
        if self.item:
            record = self.item
            self.jobs.submit(lambda: self.repo.update(record["id"], **{field: not record[field]}), lambda _: self.refresh(), self.notice)

    def change_language(self, language):
        if self.item and self.item["kind"] != "image":
            record = self.item
            self.jobs.submit(lambda: self.repo.update(record["id"], language=language, kind="code" if language and language != "text" else record["kind"]), lambda _: self.refresh(), self.notice)

    def edit_metadata(self):
        if self.item:
            record = self.item
            groups = [self.group.itemText(i) for i in range(1, self.group.count())]
            dialog = MetadataDialog(record, groups, self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                fields = dialog.fields()
                self.jobs.submit(lambda: self.repo.update(record["id"], **fields), lambda _: self.refresh(), self.notice)

    def create_group(self):
        name, accepted = QInputDialog.getText(self, "新建收藏分组", "分组名称")
        if accepted and name.strip():
            self.jobs.submit(lambda: self.repo.create_group(name.strip()), lambda _: self.refresh(), self.notice)

    def edit_content(self):
        if not self.item or self.item["kind"] == "image":
            return
        record = self.item
        dialog = QDialog(self)
        dialog.setWindowTitle("编辑原始内容")
        dialog.resize(700, 520)
        layout = QVBoxLayout(dialog)
        hint = QLabel("保存后会替换这条记录的原文。若需要保留原文，请使用文本工具另存结果。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        editor = QPlainTextEdit(record["text"])
        editor.setFont(QFont("Consolas", 11))
        layout.addWidget(editor, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if not editor.document().isModified():
                return
            text = editor.toPlainText()
            if not text:
                self.notice("不能保存空记录")
                return
            self.jobs.submit(lambda: self.repo.update(record["id"], text=text), lambda _: (self.notice("原文已保存"), self.refresh()), self.notice)

    def tools(self):
        if self.item and self.item["kind"] == "image":
            return
        TextToolsDialog(self.item.get("text", "") if self.item else "", self.jobs, self.copy_text, self.save_text, self).exec()

    def toggle_pause(self):
        self.monitor.set_paused(not self.monitor.paused)
        try:
            self.settings.set("paused", self.monitor.paused)
            self.settings.save()
        except Exception:
            self.notice("记录状态已改变，但设置未能写入磁盘")
        self.update_pause()

    def update_pause(self):
        self.pause.setText("恢复记录" if self.monitor.paused else "暂停记录")
        self.local.setText("记录已暂停" if self.monitor.paused else "正在记录 · 仅本机保存")
        if hasattr(self, "tray_pause"):
            self.tray_pause.setText("恢复记录" if self.monitor.paused else "暂停记录")

    def open_settings(self):
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        old = {key: self.settings.get(key) for key in values}
        success, message = self.hotkey.register(values["hotkey"])
        if not success:
            QMessageBox.warning(self, "快捷键无法注册", message + "\n原设置继续生效。")
            return
        registry_changed = False
        try:
            if values["startup"] != old["startup"]:
                set_startup(values["startup"])
                registry_changed = True
            for key, value in values.items():
                self.settings.set(key, value)
            self.settings.save()
        except Exception:
            for key, value in old.items():
                self.settings.set(key, value)
            self.hotkey.register(old["hotkey"])
            if registry_changed:
                try:
                    set_startup(old["startup"])
                except Exception:
                    pass
            QMessageBox.warning(self, "设置保存失败", "请检查数据目录权限或 Windows 启动项权限。")
            return
        apply_theme(self.app, values["theme"])
        self.quick_btn.setText(values["hotkey"])
        self.show_item(self.item)
        self.notice("设置已保存")
        self.cleanup()

    def cleanup(self):
        count, days = self.settings.get("max_items", 2000), self.settings.get("max_age_days", 30)
        self.jobs.submit(lambda: self.repo.cleanup(count, days), lambda removed: self.refresh() if removed else None, self.notice)

    def delete_item(self):
        self.actions.delete_record(self.item)

    def clear_history(self):
        self.actions.clear_all()

    def export_data(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出本地备份（含原始内容，请妥善保管）", "ClipNest-backup.zip", "ClipNest 备份 (*.zip)")
        if path:
            self.notice("正在导出…")
            self.jobs.submit(lambda: self.repo.export_archive(Path(path)), lambda _: self.notice("备份已导出"), self.notice)

    def import_data(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入 ClipNest 备份", "", "ClipNest 备份 (*.zip)")
        if path:
            self.notice("正在检查并导入…")
            self.jobs.submit(lambda: self.repo.import_archive(Path(path)), lambda count: (self.notice(f"导入完成 · {count} 条记录"), self.refresh()), self.notice)

    def closeEvent(self, event):
        if self.exiting or not hasattr(self, "tray") or not self.tray.isVisible():
            event.accept()
            if not self.exiting:
                self.app.quit()
        else:
            event.ignore()
            self.hide()
            self.notice("ClipNest 已收至托盘")
