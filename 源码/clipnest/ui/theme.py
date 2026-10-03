from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QPen
from PySide6.QtWidgets import QApplication


def app_icon():
    app = QApplication.instance()
    dark = bool(app and app.property("clipnestDark"))
    pix = QPixmap(64, 64)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#184b7e" if dark else "#2284ec"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(QRectF(1, 1, 62, 62), 18, 18)
    painter.setPen(QPen(QColor("white"), 3))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(QRectF(19, 18, 28, 34), 5, 5)
    painter.setBrush(QColor("#8baed0" if dark else "#b8e2ff"))
    painter.drawRoundedRect(QRectF(26, 12, 14, 12), 4, 4)
    painter.drawLine(26, 33, 40, 33)
    painter.drawLine(26, 41, 36, 41)
    painter.end()
    return QIcon(pix)


def apply_theme(app, mode):
    dark = mode == "dark"
    bg, panel, fg, muted, border = ("#090909", "#141414", "#f1f3f6", "#999fa9", "#2a2a2a") if dark else ("#f2f7fd", "#ffffff", "#18263b", "#697c94", "#e0e9f3")
    accent = "#6f99c1" if dark else "#4c9af0"
    focus = "#286aab" if dark else "#4698f4"
    primary = "#194b79" if dark else "#2e86e4"
    primary_hover = "#205a8e" if dark else "#2076d2"
    checked = "#245b90" if dark else "#2e86e4"
    selected = "#13243a" if dark else "#e5f0ff"
    selection = "#1f507f" if dark else "#287ad2"
    hover = "#101d2b" if dark else "#edf5ff"
    app.setProperty("clipnestDark", dark)
    app.setStyleSheet(f"""
        QWidget {{ color:{fg}; font-family:'Microsoft YaHei UI'; font-size:13px; }}
        QMainWindow, QDialog {{ background:{bg}; }}
        QDialog#quickWindow {{ background:transparent; }}
        QWidget#quickCard {{ background:{bg}; border:1px solid {border}; border-radius:14px; }}
        QWidget#dragHeader {{ background:{bg}; border:0; }}
        QWidget#sidebar {{ background:{panel}; border-right:1px solid {border}; }}
        QLabel#brand {{ font-size:25px; font-weight:700; color:{accent}; }}
        QLabel#heading {{ font-size:23px; font-weight:600; }}
        QLabel#muted {{ color:{muted}; }}
        QLabel#detailTitle {{ font-size:19px; font-weight:600; }}
        QLineEdit, QPlainTextEdit, QTextBrowser, QSpinBox, QComboBox {{ background:{panel}; border:1px solid {border}; border-radius:8px; padding:8px; selection-background-color:{selection}; selection-color:white; }}
        QLineEdit:focus, QPlainTextEdit:focus {{ border:1px solid {focus}; }}
        QComboBox::drop-down {{ border:0; width:24px; }}
        QComboBox QAbstractItemView {{ background:{panel}; border:1px solid {border}; selection-background-color:{selected}; selection-color:{fg}; }}
        QCheckBox {{ spacing:6px; }}
        QCheckBox::indicator {{ width:15px; height:15px; border:1px solid {muted}; border-radius:4px; background:{panel}; }}
        QCheckBox::indicator:checked {{ background:{checked}; border-color:{checked}; }}
        QListWidget {{ background:{panel}; border:1px solid {border}; border-radius:10px; outline:0; padding:5px; }}
        QListView::item:selected {{ background:{selected}; color:{fg}; }}
        QListView::item:hover {{ background:{hover}; }}
        QListWidget#navigation {{ border:0; background:transparent; }}
        QListWidget#navigation::item {{ padding:12px 10px; border-radius:8px; margin:2px; }}
        QListWidget#navigation::item:selected {{ background:{selected}; color:{accent}; }}
        QPushButton {{ background:{panel}; border:1px solid {border}; border-radius:8px; padding:8px 12px; }}
        QPushButton:hover {{ border-color:{focus if dark else '#529cf0'}; background:{hover}; }}
        QPushButton:disabled {{ color:{muted}; }}
        QPushButton#primary {{ background:{primary}; color:white; border:0; font-weight:600; }}
        QPushButton#primary:hover {{ background:{primary_hover}; }}
        QPushButton#danger {{ color:#e15f68; }}
        QStatusBar {{ color:{muted}; background:{bg}; }}
        QSplitter::handle {{ background:{bg}; width:10px; }}
        QScrollBar:vertical {{ background:transparent; width:8px; }}
        QScrollBar::handle:vertical {{ background:{border}; border-radius:4px; min-height:25px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
        QMenu {{ background:{panel}; border:1px solid {border}; padding:6px; }}
        QMenu::item {{ padding:8px 24px; }}
        QMenu::item:selected {{ background:{selected}; }}
        QToolTip {{ background:{panel}; color:{fg}; border:1px solid {border}; }}
    """)
