# ClipNest

ClipNest 是一个 Windows 剪贴板管理器，使用 Python、PySide6 和 SQLite。它保存新复制的文本、代码、链接、邮箱、颜色和图片，支持搜索、收藏、置顶和再次复制。默认快捷键是 `Ctrl + Alt + V`。

## 从源码运行

在仓库根目录打开 PowerShell，先进入源码目录：

```powershell
Set-Location .\源码
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run.ps1
```

开发环境需要 Windows 10/11 和 Python 3.12 或更新版本。测试和打包命令、Windows 验收记录见[发布说明](源码/docs/RELEASE.md)。

## 数据与隐私

数据默认保存在 `%LOCALAPPDATA%\ClipNest`。应用不连接服务器，不需要账号，也没有遥测或在线 AI 功能。启动时不会读取剪贴板里已有的内容，只记录启动监听后的新复制内容。托盘菜单可以暂停记录。

剪贴板历史和导出文件会包含复制过的原文及图片；当前版本没有数据库加密。使用敏感内容前，请暂停记录，并妥善保管数据目录和导出文件。更完整的操作说明在[源码目录的 README](源码/README.md)。

## 项目文件

应用源码、测试、构建脚本和第三方许可文件都在 [`源码`](源码/) 目录中。第三方组件清单见[许可说明](源码/docs/licenses/THIRD_PARTY_NOTICES.md)。

ClipNest 本身尚未指定开源许可证。公开仓库不等于授予代码使用、修改或再发布的许可；请在选择许可证后再补充 `LICENSE` 文件。