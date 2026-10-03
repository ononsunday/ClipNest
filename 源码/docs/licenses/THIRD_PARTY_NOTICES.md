# 第三方组件与许可文本

本目录保留当前构建环境实际安装的第三方包所带的许可文件，并附上 Python 安装目录的原始 `LICENSE.txt`。文件保持原文，未做翻译或改写。构建脚本把整个目录复制到目录版的 `docs\licenses` 文件夹。

这份清单记录组件和文件来源，没有替 ClipNest 项目选择许可证，也没有完成全部 Qt 模块和内置第三方代码的发布许可审查。对外正式发布前，需要根据最终打包文件确认适用许可、版权声明和对应源码提供方式。

| 组件 | 当前版本与用途 | 文件与来源 |
| --- | --- | --- |
| Python | 3.13.14，应用解释器和标准库 | `Python-3.13.14-LICENSE.txt`，直接复制安装目录文件；[Python 许可说明](https://docs.python.org/3/license.html)。 |
| PySide6、Essentials、Addons、Shiboken6 | 6.11.2，Qt Python 绑定及 Qt 运行库 | 各版本目录包含原 wheel 的 `LicenseRef-Qt-Commercial.txt`。包元数据另列出 `LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only`；商用文本的存在不表示本项目持有商用授权。[Qt for Python 许可说明](https://doc.qt.io/qtforpython-6/licenses.html)。 |
| Pygments | 2.21.0，代码识别和高亮 | `Pygments-2.21.0/licenses/LICENSE` 与 `AUTHORS`，直接复制已安装包；[官方许可文本](https://github.com/pygments/pygments/blob/master/LICENSE)。 |
| PyInstaller | 6.22.3，构建工具及嵌入应用的 bootloader | `PyInstaller-6.22.3/licenses/COPYING.txt`，包含 bootloader 例外；[官方许可说明](https://pyinstaller.org/en/stable/license.html)。 |
| SQLite | Python 标准库 `_sqlite3` 所用的本地数据库引擎 | SQLite 的公开许可说明为公有领域；[官方版权说明](https://sqlite.org/copyright.html)。实际运行库版本可用 `sqlite3.sqlite_version` 查询。 |
| 测试和构建辅助包 | pytest、pluggy、iniconfig、colorama、packaging、altgraph、pefile、pywin32-ctypes、setuptools、pyinstaller-hooks-contrib | 各组件的版本目录保留已安装包许可；它们不都属于最终运行时依赖。具体文件和哈希见 `manifest.json`。 |

PySide6 wheel 的许可目录没有附带其元数据所列的 GNU 开源许可全文。因此补充了 `GNU/LGPL-3.0.txt`、`GNU/GPL-2.0.txt` 和 `GNU/GPL-3.0.txt`。前两份来自 [GNU LGPL 3](https://www.gnu.org/licenses/lgpl-3.0.txt) 与 [GNU GPL 2](https://www.gnu.org/licenses/old-licenses/gpl-2.0.txt)；GPL 3 来自 [Qt 6.11 官方仓库](https://code.qt.io/cgit/qt/qtbase.git/plain/LICENSES/GPL-3.0-only.txt?h=6.11.2)，内容与取得的镜像副本哈希一致。

ClipNest 使用 Qt Core、GUI 和 Widgets。打包 hook 在依赖收集时排除未使用的 PDF 图像插件和虚拟键盘插件。最终目录版已检查，不含 QtPdf、QtVirtualKeyboard、QtQml、QtQuick DLL，也不含这两个插件。Qt 内置的第三方图像、字体及其他代码仍需要在正式发布时核对相应版权声明；本清单没有宣称已覆盖所有这些代码。
