# HoverBar

半透明 Windows 任务栏监控条 —— 实时显示 CPU / GPU / 内存 / 网速。

---

## 功能

| | |
|---|---|
| 🖥 **CPU** | 使用率 + 频率 |
| 🎮 **GPU** | 使用率 + 显存（NVIDIA） |
| 📊 **内存** | 使用率 + 已用/总量 |
| 🌐 **网速** | 下载/上传速率 |
| 🎨 **外观** | 半透明深色，无边框，始终置顶 |
| 🖱 **交互** | 拖拽移动，双击复位 |
| 📋 **菜单** | 右键选择显示/隐藏区块 |
| 🔧 **布局** | 宽度自适应，高度跟随任务栏 |

## 快速开始

本仓库**不包含** `HoverBar.exe`（已 gitignore），需自行构建（见 [构建](#构建)），或直接以源码运行（见 [使用](#使用)）。

## 开发

需要 Python 3.10+。

```bash
git clone https://github.com/anzye2016/hoverbar.git
cd hoverbar
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

## 使用

```bash
# 源码运行（首次自动装依赖）
start.bat

# 或直接运行
.venv\Scripts\pythonw.exe hoverbar.pyw

# 已构建的 exe（双击）
HoverBar.exe
```

监控条停靠在屏幕左下角、任务栏上方，高度自动匹配任务栏；右键菜单可选择显示/隐藏。

## 构建

```bash
双击 build.bat        # 一键编译（推荐）
# 或手动编译：
.venv\Scripts\pip install pyinstaller
.venv\Scripts\python.exe -m PyInstaller HoverBar.spec
```

输出：单文件 `HoverBar.exe`，位于项目根目录。

## 开机自启

1. `Win + R` → `shell:startup`
2. 将 `HoverBar.exe` 或 `start.vbs` 的快捷方式放入该文件夹

## 依赖

| 包 | 用途 |
|---|---|
| [PySide6](https://pypi.org/project/PySide6/) | Qt GUI 框架 |
| [psutil](https://pypi.org/project/psutil/) | CPU / 内存 / 网速 |
| [nvidia-ml-py](https://pypi.org/project/nvidia-ml-py/) | NVIDIA GPU 监控 |
| [pywin32](https://pypi.org/project/pywin32/) | Windows API |

## 截图

![截图](screenshots/preview.png)

## 许可证

MIT License —— 详见 [LICENSE](LICENSE)
