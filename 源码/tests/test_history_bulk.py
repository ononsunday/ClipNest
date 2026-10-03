"""Qt 列表交互只使用合成记录，不操作剪贴板或发送系统输入。"""
import pytest
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QListWidgetItem

from clipnest.ui.history import HistoryList
from clipnest.ui.theme import apply_theme


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def history(app):
    apply_theme(app, "light")
    view = HistoryList()
    view.resize(380, 330)
    for number in range(3):
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, {"id": str(number), "text": "这是一段用于验证按钮布局的长文本 " * 8,
                    "kind": "text", "last_copied_at": "2026-10-03T00:00:00+00:00", "tags": ["测试"]})
        view.addItem(item)
    view.show()
    app.processEvents()
    yield view
    view.close()
    app.processEvents()


def body_point(view, row=0):
    rect = view.visualItemRect(view.item(row))
    return QPoint(rect.left() + 65, rect.center().y())


def test_delete_hit_does_not_select_click_or_activate(history):
    history.setProperty("singleAction", True)
    history.setCurrentRow(1)
    events, deleted, activated = [], [], []
    history.itemClicked.connect(events.append)
    history.itemDoubleClicked.connect(events.append)
    history.delete_requested.connect(deleted.append)
    history.activate_requested.connect(activated.append)
    point = history.delete_button_rect(history.item(0)).center()
    QTest.mouseClick(history.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert [record["id"] for record in deleted] == ["0"]
    assert events == activated == []
    assert history.currentRow() == 1


def test_delete_press_release_outside_cancels(history):
    deleted = []
    history.delete_requested.connect(deleted.append)
    QTest.mousePress(history.viewport(), Qt.MouseButton.LeftButton,
                     pos=history.delete_button_rect(history.item(0)).center())
    QTest.mouseRelease(history.viewport(), Qt.MouseButton.LeftButton, pos=body_point(history, 1))
    assert deleted == []


def test_single_action_only_left_body_release_activates(history):
    history.setProperty("singleAction", True)
    activated = []
    history.activate_requested.connect(activated.append)
    QTest.mouseClick(history.viewport(), Qt.MouseButton.RightButton, pos=body_point(history, 1))
    assert activated == []
    QTest.mouseClick(history.viewport(), Qt.MouseButton.LeftButton, pos=body_point(history, 2))
    assert [record["id"] for record in activated] == ["2"]


def test_single_action_double_click_does_not_activate_twice(history):
    history.setProperty("singleAction", True)
    activated, doubled = [], []
    history.activate_requested.connect(activated.append)
    history.itemDoubleClicked.connect(doubled.append)
    point = body_point(history)
    # 显式模拟真实双击的第一次完整单击和第二次双击事件/释放。
    QTest.mouseClick(history.viewport(), Qt.MouseButton.LeftButton, pos=point)
    QTest.mouseDClick(history.viewport(), Qt.MouseButton.LeftButton, pos=point)
    QTest.mouseRelease(history.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert len(activated) == 1
    assert doubled == []


def test_body_drag_does_not_activate(history):
    history.setProperty("singleAction", True)
    activated = []
    history.activate_requested.connect(activated.append)
    start = body_point(history)
    QTest.mousePress(history.viewport(), Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(history.viewport(), start + QPoint(40, 0))
    QTest.mouseRelease(history.viewport(), Qt.MouseButton.LeftButton, pos=start + QPoint(40, 0))
    assert activated == []


def test_bulk_selection_toggles_without_activation(history):
    history.setProperty("singleAction", True)
    activated, doubled = [], []
    history.activate_requested.connect(activated.append)
    history.itemDoubleClicked.connect(doubled.append)
    history.setCurrentRow(2)
    history.set_bulk_mode(True)
    assert history.is_bulk_mode()
    assert history.selected_records() == []
    QTest.mouseClick(history.viewport(), Qt.MouseButton.LeftButton, pos=body_point(history, 0))
    QTest.mouseClick(history.viewport(), Qt.MouseButton.LeftButton, pos=body_point(history, 2))
    assert [record["id"] for record in history.selected_records()] == ["0", "2"]
    QTest.mouseClick(history.viewport(), Qt.MouseButton.LeftButton, pos=body_point(history, 0))
    assert [record["id"] for record in history.selected_records()] == ["2"]
    QTest.mouseDClick(history.viewport(), Qt.MouseButton.LeftButton, pos=body_point(history, 1))
    QTest.mouseRelease(history.viewport(), Qt.MouseButton.LeftButton, pos=body_point(history, 1))
    assert activated == doubled == []
    history.set_bulk_mode(False)
    assert not history.is_bulk_mode()
    assert history.selected_records() == []


@pytest.mark.parametrize("mode", ["light", "dark"])
@pytest.mark.parametrize("bulk", [False, True])
def test_history_paints_controls_at_narrow_width(history, app, mode, bulk):
    apply_theme(app, mode)
    history.resize(260, 330)
    history.set_bulk_mode(bulk)
    history.item(0).setSelected(True)
    app.processEvents()
    image = QImage(history.size(), QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        history.render(painter, QPoint())
    finally:
        painter.end()
    assert not image.isNull()
    assert history.delete_button_rect(history.item(0)).right() < history.viewport().width()

