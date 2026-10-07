"""应用生命周期：单实例、托盘、后台保存、正常资源释放。"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from PySide6.QtCore import QTimer, QLockFile, QStandardPaths
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon, QMenu
from clipnest.core.repository import Repository
from clipnest.platform.settings import Settings
from clipnest.platform.clipboard import ClipboardMonitor
from clipnest.platform.windows import HotkeyManager, bring_to_front
from clipnest.ui.jobs import Jobs
from clipnest.ui.theme import app_icon, apply_theme
from clipnest.ui.windows import MainWindow


def default_data_dir():
    return Path(os.environ.get("LOCALAPPDATA", QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))) / "ClipNest"


def smoke_test(app, directory):
    """只使用合成数据，不启动真实剪贴板监听，不读取已有剪贴板。"""
    from PySide6.QtGui import QImage, QColor
    from clipnest.core.text_tools import transform
    directory.mkdir(parents=True, exist_ok=True)
    settings = Settings(directory)
    settings.set("paused", True)
    repo = Repository(directory / "history.db", directory / "images")
    text = "-- 学习资料\nSELECT name, score\nFROM students\nWHERE score >= 60;\n"
    first = repo.add_text(text, "smoke-test")
    duplicate = repo.add_text(text, "smoke-test")
    assert first["id"] == duplicate["id"]
    assert len(repo.search("学习资料")) == 1
    repo.update(first["id"], favorite=True, pinned=True, title="常用 SQL 查询",
                language="SQL", kind="code", tags=["SQL", "学习"], group_name="验证")
    repo.add_text("会议要点\n1. 整理学习资料\n2. 完成课程练习", "smoke-test")
    repo.add_text("https://example.com/docs", "smoke-test")
    repo.add_text("def greet(name):\n    return f'Hello, {name}'\n", "smoke-test")
    image = QImage(120, 80, QImage.Format.Format_ARGB32)
    image.fill(QColor("#6eb7ff"))
    picture = repo.add_image(image, "smoke-test")
    assert Path(picture["image_path"]).is_file()
    assert transform('{"ok":true}', "json")
    jobs = Jobs()
    monitor = ClipboardMonitor(settings)
    hotkey = HotkeyManager()
    apply_theme(app, "light")
    window = MainWindow(repo, settings, jobs, monitor, hotkey, app)
    window.pane.requested_id = first["id"]
    window.pane.refresh()
    window.setWindowIcon(app_icon())
    window.show()
    for _ in range(80):
        app.processEvents()
        from time import sleep
        sleep(0.025)
    assert window.isVisible() and window.pane.list.count() >= 2
    window.grab().save(str(directory / "light.png"))
    assert all(not window.nav.item(i).icon().isNull() for i in range(7))
    assert not window.pause.icon().isNull() and not window.hero._pixmap.isNull()
    settings.set("theme", "dark")
    apply_theme(app, "dark")
    window.show_item(window.item)
    for _ in range(20):
        app.processEvents()
        sleep(0.025)
    app.processEvents()
    window.grab().save(str(directory / "dark.png"))
    assert window.portrait.isVisible() and not window.hero.isVisible()
    window.quick.pane.requested_id = first["id"]
    window.quick.summon()
    for _ in range(80):
        app.processEvents()
        from time import sleep
        sleep(0.025)
        if not window.quick.pane.busy and window.quick.pane.list.count() >= 2:
            break
    window.quick.grab().save(str(directory / "quick.png"))
    assert window.quick.isVisible() and window.quick.pane.list.count() >= 2
    assert window.quick.passive and not window.quick.pane.search.hasFocus()
    window.quick.pane.bulk_button.setChecked(True)
    window.quick.pane.list.selectAll()
    app.processEvents()
    assert len(window.quick.pane.list.selected_records()) == 5
    window.quick.grab().save(str(directory / "quick-bulk.png"))
    window.quick.pane.bulk_button.setChecked(False)
    settings.set("theme", "light")
    apply_theme(app, "light")
    app.processEvents()
    window.quick.grab().save(str(directory / "quick-light.png"))
    assert not window.quick.header_art._pixmap.isNull()
    # 收藏空态使用真实筛选和隔离数据，不启动监听或触碰系统剪贴板。
    repo.update(first["id"], favorite=False)
    window.quick.choose_chip("favorite")
    window.quick.pane.refresh()
    for _ in range(100):
        app.processEvents()
        sleep(0.025)
        if not window.quick.pane.busy:
            break
    assert window.quick.pane.results_stack.currentWidget() is window.quick.pane.empty
    assert window.quick.pane.empty_title.text() == "收藏夹为空"
    window.quick.grab().save(str(directory / "quick-empty.png"))
    repo.update(first["id"], favorite=True)
    window.quick.choose_chip("")
    window.quick.pane.search.setText("不存在的布局测试关键词")
    window.quick.pane.refresh()
    for _ in range(100):
        app.processEvents()
        sleep(0.025)
        if not window.quick.pane.busy:
            break
    assert window.quick.pane.empty_title.text() == "没有找到匹配内容"
    assert not window.quick.pane.empty_art.isVisible()
    window.quick.grab().save(str(directory / "quick-no-results.png"))
    from clipnest.ui.dialogs import SettingsDialog
    options = SettingsDialog(settings)
    options.show()
    app.processEvents()
    assert options.values()["close_after_paste"] is False
    options.grab().save(str(directory / "settings.png"))
    options.hide()
    window.quick.activate_search()
    app.processEvents()
    assert window.quick.pane.search.hasFocus()
    window.quick.hide()
    window.exiting = True
    window.close()
    monitor.stop()
    hotkey.unregister()
    jobs.close()
    (directory / "smoke-result.json").write_text(json.dumps({"passed": True, "native_platform": app.platformName(), "records": repo.stats()["total"], "screens": len(app.screens()), "device_pixel_ratio": app.primaryScreen().devicePixelRatio(), "theme_assets": True, "navigation_icons": 8}, indent=2), encoding="utf-8")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="ClipNest 本地剪贴板管理器")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--start-hidden", action="store_true")
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--quit-after", type=float)
    args = parser.parse_args(argv)
    app = QApplication([sys.argv[0]])
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    app.setApplicationName("ClipNest")
    app.setOrganizationName("ClipNest")
    app.setWindowIcon(app_icon())
    app.setQuitOnLastWindowClosed(False)
    if args.smoke_test:
        if args.data_dir:
            # 烟测必须使用全新目录，防止覆盖或导出已有用户历史。
            if args.data_dir.exists() and any(args.data_dir.iterdir()):
                return 2
            return smoke_test(app, args.data_dir)
        with tempfile.TemporaryDirectory(prefix="ClipNest-smoke-") as directory:
            return smoke_test(app, Path(directory))
    data_dir = args.data_dir or default_data_dir()
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        lock = QLockFile(str(data_dir / "instance.lock"))
        lock.setStaleLockTime(30000)
        if not lock.tryLock(100):
            QMessageBox.information(None, "ClipNest 已运行", "请通过系统托盘或全局快捷键打开已有窗口。")
            return 0
        settings = Settings(data_dir)
        repo = Repository(data_dir / "history.db", data_dir / "images")
    except Exception:
        QMessageBox.critical(None, "无法打开数据目录", "请检查目录权限或磁盘空间。可以用 --data-dir 指定其他目录。")
        return 1
    jobs = Jobs()
    monitor = ClipboardMonitor(settings)
    hotkey = HotkeyManager()
    apply_theme(app, settings.get("theme", "light"))
    window = MainWindow(repo, settings, jobs, monitor, hotkey, app)
    window.setWindowIcon(app_icon())
    tray = QSystemTrayIcon(app_icon(), window)
    tray.setToolTip("ClipNest · 剪贴板历史")
    menu = QMenu()
    menu.addAction("打开 ClipNest", lambda: bring_to_front(window))
    menu.addAction("快捷搜索", window.quick.summon)
    window.tray_pause = menu.addAction("暂停记录", window.toggle_pause)
    menu.addAction("设置", window.open_settings)
    menu.addSeparator()

    def quit_app():
        window.exiting = True
        window.quick.hide()
        app.quit()
    menu.addAction("完全退出", quit_app)
    tray.setContextMenu(menu)
    tray.activated.connect(lambda reason: bring_to_front(window) if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
    window.tray = tray
    if QSystemTrayIcon.isSystemTrayAvailable():
        tray.show()
    window.update_pause()
    ok, message = hotkey.register(settings.get("hotkey", "Ctrl+Alt+V"))
    if not ok:
        QTimer.singleShot(300, lambda: QMessageBox.warning(window, "快捷键未启用", message))
    if settings.load_warning:
        QTimer.singleShot(600, lambda: QMessageBox.warning(window, "设置未能读取", settings.load_warning))
    monitor.error.connect(window.notice)
    capture_refresh = QTimer(window)
    capture_refresh.setSingleShot(True)
    capture_refresh.setInterval(180)
    capture_refresh.timeout.connect(lambda: (window.cleanup(), window.refresh()))

    def saved(_):
        capture_refresh.start()
    monitor.captured_text.connect(lambda text, source: jobs.submit(lambda: repo.add_text(text, source), saved, window.notice))
    monitor.captured_image.connect(lambda image, source: jobs.submit(lambda: repo.add_image(image, source), saved, window.notice))
    monitor.start()
    cleanup_timer = QTimer(window)
    cleanup_timer.setInterval(60 * 60 * 1000)
    cleanup_timer.timeout.connect(window.cleanup)
    cleanup_timer.start()
    window.cleanup()
    app.aboutToQuit.connect(monitor.stop)
    app.aboutToQuit.connect(hotkey.unregister)
    app.aboutToQuit.connect(tray.hide)
    app.aboutToQuit.connect(jobs.close)
    app.aboutToQuit.connect(lock.unlock)
    if not args.start_hidden or not tray.isVisible():
        window.show()
    if args.quit_after is not None:
        QTimer.singleShot(max(1, int(args.quit_after * 1000)), quit_app)

    def unhandled(exc_type, exc, tb):
        # 不记录异常正文，避免原文在第三方组件异常中被泄露。
        window.notice(f"发生未处理错误（{exc_type.__name__}），请重试；持续失败时退出并重启。")
    sys.excepthook = unhandled
    return app.exec()
