# HoverBar

半透明 Windows 任务栏监控条 —— 实时显示 CPU / GPU / 内存 / 网速。

---

## 功能

| | |
|---|---|
| 🖥 **CPU** | 使用率 + 频率 + 温度（需 Core Temp） |
| 🎮 **GPU** | 使用率 + 显存 + 温度（NVIDIA） |
| 📊 **内存** | 使用率 + 已用/总量 |
| 🌐 **网速** | 下载/上传速率 |
| 🌡 **温度** | 显示在进度条下方，随冷热变色 |
| 🎨 **外观** | 半透明深色，无边框，始终置顶 |
| 🖱 **交互** | 拖拽移动，双击复位 |
| 📋 **菜单** | 右键选择显示/隐藏区块 |
| 🔧 **布局** | 宽度自适应，高度跟随任务栏 |

## 温度显示

百分比在进度条上方，温度在进度条下方、正对上方百分比，并按冷热变色（< 60°C 灰 → 60°C 黄 → 75°C 橙 → 85°C 红）。

| 指标 | 来源 | 要求 |
|---|---|---|
| **GPU 温度** | NVML | 有 NVIDIA 驱动即可直读，无需额外软件 |
| **CPU 温度** | [Core Temp](https://www.alcpu.com/CoreTemp/) 共享内存 | 需 Core Temp 在后台运行 |

CPU 温度为什么要借助 Core Temp：Windows 的 WMI / 驱动层只能拿到主板（封装附近）温度，读不准核心温度，而 Core Temp 用内核驱动读 CPU 内部传感器，读数才准确。

HoverBar 直接读取 Core Temp 的共享内存（`CoreTempMappingObjectEx`），取所有核心中的最高值 —— 不注入进程、不装插件、也不额外占 CPU：

- Core Temp **在后台运行** → 显示 CPU 温度
- Core Temp **未运行** → 自动隐藏 CPU 温度，其余指标照常（不会显示过期数值）

> Core Temp 保持「启动时最小化到通知区域」即可，无需窗口可见。

### 让 Core Temp 随系统启动

Core Temp 自带的「随 Windows 启动」开关在部分安装方式下不生效（勾选点应用、重进设置又变回未勾选），而且它的清单要求管理员权限 —— 走「启动」文件夹或 Run 注册表项，每次登录都会弹一次 UAC。正确做法是建一个**以最高权限运行**的计划任务，登录后静默启动、不弹 UAC：

```powershell
# 用「管理员身份」打开 PowerShell 执行一次
$exe = 'C:\Program Files\CoreTemp\Core Temp x64.exe'      # ← 改成你的实际路径
$me  = "$env:USERDOMAIN\$env:USERNAME"
$act = New-ScheduledTaskAction -Execute $exe -WorkingDirectory (Split-Path $exe)
$trg = New-ScheduledTaskTrigger -AtLogOn -User $me
$trg.Delay = 'PT20S'                                       # 等桌面加载完再启动
$prn = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Highest
$set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                                    -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName 'Core Temp' -Action $act -Trigger $trg `
                       -Settings $set -Principal $prn -Force
```

任务里写死了 exe 路径：之后移动位置或重装到别的目录，需要重建任务。删掉它执行 `Unregister-ScheduledTask -TaskName 'Core Temp'`。

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
| [nvidia-ml-py](https://pypi.org/project/nvidia-ml-py/) | NVIDIA GPU 监控（利用率 / 显存 / 温度） |
| [pywin32](https://pypi.org/project/pywin32/) | Windows API |

可选（非 pip 依赖）：[Core Temp](https://www.alcpu.com/CoreTemp/) —— 运行它才显示 CPU 温度。

## 截图

![截图](screenshots/preview.png)

## 许可证

MIT License —— 详见 [LICENSE](LICENSE)
