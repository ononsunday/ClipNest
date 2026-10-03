"""只排除本软件不使用的 PDF 图像和虚拟键盘插件。"""

from pathlib import Path

from PyInstaller.utils.hooks.qt import add_qt6_dependencies


hiddenimports, binaries, datas = add_qt6_dependencies(__file__)

# 先阻止插件进入依赖收集，避免连带打包 QtPdf 和 QtVirtualKeyboard。
_unused_plugins = {"qpdf.dll", "qtvirtualkeyboardplugin.dll"}
binaries = [entry for entry in binaries if Path(entry[0]).name.casefold() not in _unused_plugins]
