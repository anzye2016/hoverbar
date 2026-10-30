#!/usr/bin/env python3
"""
HoverBar - 桌面系统监控条
实时显示 CPU/GPU/内存/网速，悬停在任务栏上方
半透明无边框，支持拖拽和右键菜单
"""

import sys
import os
import json
import struct
import traceback
from datetime import datetime

# ── Qt ──
from PySide6.QtWidgets import (
    QApplication, QWidget, QHBoxLayout, QVBoxLayout,
    QLabel, QFrame, QMenu,
)
from PySide6.QtCore import (
    Qt, QTimer, Signal, QObject, QPoint,
)
from PySide6.QtGui import (
    QFont, QColor, QPainter, QPen,
    QAction,
)

# ── Windows API（强制置顶） ──
import ctypes
from ctypes import wintypes

HWND_TOPMOST   = -1
HWND_NOTOPMOST = -2
SWP_NOMOVE     = 0x0002
SWP_NOSIZE     = 0x0001
SWP_NOACTIVATE = 0x0010

_user32 = ctypes.windll.user32

# ── 系统监控 ──
import psutil

try:
    import win32pdh
    PDH_OK = True
except Exception:
    PDH_OK = False

# ── NVIDIA GPU ──
try:
    from pynvml import (
        nvmlInit, nvmlDeviceGetHandleByIndex,
        nvmlDeviceGetUtilizationRates, nvmlDeviceGetMemoryInfo,
        nvmlDeviceGetTemperature, NVML_TEMPERATURE_GPU,
        nvmlDeviceGetPowerUsage,
        nvmlShutdown, nvmlDeviceGetCount,
    )
    NVML_OK = True
except Exception:
    NVML_OK = False


# ════════════════════════════════════════════════════════════════════
#  常量
# ════════════════════════════════════════════════════════════════════

UPDATE_MS = 1500          # 刷新间隔（毫秒）
WIDGET_HEIGHT = 44        # 控件高度（main 中按任务栏高度自适应）

# ── 精致暗色调色板（去饱和、温润） ──
COLOR_BG     = QColor(22, 22, 26, 220)   # 暖暗底
COLOR_BORDER = QColor(55, 55, 62, 90)    # 极淡边框
COLOR_TEXT   = QColor(225, 225, 232)     # 主文字
COLOR_DIM    = QColor(135, 135, 148)     # 辅助文字
COLOR_BAR_BG = QColor(55, 55, 62, 100)  # 进度条底

# 指标色 — 低饱和、有质感
COLOR_CPU    = QColor(96,  165, 250)     # 柔蓝
COLOR_GPU    = QColor(251, 113, 133)     # 柔红
COLOR_MEM    = QColor(52,  211, 153)     # 柔绿
COLOR_NET    = QColor(167, 139, 250)     # 柔紫


# 温度配色阈值 (色变黄, 变橙, 变红) °C — CPU / GPU 各自的温度墙不同，分开设
TEMP_BANDS_CPU = (65, 80, 90)     # TjMax≈95
TEMP_BANDS_GPU = (65, 70, 80)     # 65 起变黄，80 就算烫


def _temp_color(t: float, bands: tuple[int, int, int] = TEMP_BANDS_CPU) -> QColor:
    """温度配色：常温低调灰，越热越醒目"""
    warn, hot, crit = bands
    if t >= crit:
        return QColor(235, 80, 80)       # 烫
    if t >= hot:
        return QColor(255, 145, 60)      # 热
    if t >= warn:
        return QColor(255, 190, 70)      # 温热
    return COLOR_DIM                     # 常温不抢眼


# 字体 — 优先系统高品质字体
# 数据字体用 Consolas：Cascadia Code 的粗体在 8~9pt 会把数字 5 糊成 6
FONT_UI     = "Segoe UI Variable Display, Segoe UI, Segoe UI Variable Text"
FONT_DATA   = "Consolas, Cascadia Code, Courier New"

# ── 路径（兼容 PyInstaller 打包） ──
if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(APP_DIR, "hoverbar.log")
CONFIG_FILE = os.path.join(APP_DIR, "hoverbar.json")

def log(msg: str) -> None:
    try:
        # 日志轮转：超 1MB 截断保留末尾 2000 行
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 1024 * 1024:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.writelines(lines[-2000:])
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%H:%M:%S}] {msg}\n")
    except Exception:
        pass


# ════════════════════════════════════════════════════════════════════
#  数据采集
# ════════════════════════════════════════════════════════════════════

# ── Core Temp 共享内存 ──
#
# Core Temp 在后台运行时才会创建映射对象，进程退出映射随即消失，
# 因此 OpenFileMappingW 失败 == Core Temp 未运行（此时不显示 CPU 温度）。
#
# 结构（实测 Core Temp x64，与官方 SharedMem 文档布局一致，
# 但温度 / 频率为 float 而非 DWORD）：
#     0x000  DWORD uiLoad[256]     各核心负载 %
#     0x400  DWORD uiTjMax[128]    各 CPU 的 TjMax
#     0x600  DWORD uiCoreCnt       核心数
#     0x604  DWORD uiCPUCnt        CPU（封装）数
#     0x608  float uiTemp[256]     各核心温度 °C
#     0xA18  char  ucCPUName        CPU 名称（ANSI）
#     0xC84  float                 封装功耗 W（AMD 实测，Intel 未验证）
#     0xE84  float                 各核心频率 GHz×10

_CT_MAP_NAMES  = ("CoreTempMappingObjectEx", "CoreTempMappingObject")
_CT_READ_SIZE  = 4096
_CT_OFF_CORES  = 0x600
_CT_OFF_CPUS   = 0x604
_CT_OFF_TEMP   = 0x608
_CT_OFF_NAME   = 0xA18
_CT_OFF_POWER  = 0xC84
_FILE_MAP_READ = 0x0004


class CoreTempReader:
    """从 Core Temp 的共享内存读取 CPU 温度与功耗。

    read() 返回 (最高核心温度, 封装功耗 W)；Core Temp 未运行时两者都是 None。
    每次读取都「开映射 → 读 → 关」，因此不会读到 Core Temp 退出后的僵尸数据。
    """

    def __init__(self) -> None:
        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        k32.OpenFileMappingW.restype = wintypes.HANDLE
        k32.OpenFileMappingW.argtypes = (
            wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
        k32.MapViewOfFile.restype = ctypes.c_void_p
        k32.MapViewOfFile.argtypes = (
            wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
            wintypes.DWORD, ctypes.c_size_t)
        k32.UnmapViewOfFile.argtypes = (ctypes.c_void_p,)
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)
        self._k32 = k32
        self._name: str | None = None    # 已验证可用的映射名（省一次尝试）

    # ── 取一份原始快照 ──

    def _snapshot(self) -> bytes | None:
        names = (self._name,) if self._name else _CT_MAP_NAMES
        for name in names:
            h = self._k32.OpenFileMappingW(_FILE_MAP_READ, False, name)
            if not h:
                continue
            buf = None
            try:
                view = self._k32.MapViewOfFile(h, _FILE_MAP_READ, 0, 0, 0)
                if view:
                    try:
                        buf = ctypes.string_at(view, _CT_READ_SIZE)
                    finally:
                        self._k32.UnmapViewOfFile(ctypes.c_void_p(view))
            finally:
                self._k32.CloseHandle(h)
            if buf is not None:
                self._name = name
                return buf

        self._name = None            # 下次重新探测
        return None

    # ── 温度 + 功耗 ──

    def read(self) -> tuple[float | None, float | None]:
        """返回 (最高核心温度 °C, 封装功耗 W)，取不到则为 None"""
        buf = self._snapshot()
        if buf is None:
            return None, None
        try:
            cores, cpus = struct.unpack_from('<2I', buf, _CT_OFF_CORES)
        except struct.error:
            return None, None
        n = cores * max(1, cpus)
        if not 1 <= n <= 256:
            return None, None

        temp = None
        # 温度数组类型随版本而异：新版 float，旧版 DWORD；取值域筛选区分
        for code in ('f', 'I'):
            try:
                vals = struct.unpack_from(f'<{n}{code}', buf, _CT_OFF_TEMP)
            except struct.error:
                continue
            good = [v for v in vals if 1.0 <= v <= 150.0]
            if len(good) >= max(1, n // 2):      # 过半合理即认为解析正确
                temp = max(good)
                break

        # 封装功耗只在 AMD 平台验证过（Ryzen 7 5700X，与 Core Temp 界面读数一致）；
        # 其它平台该偏移含义未知，宁可不显示也不给错数
        power = None
        name = buf[_CT_OFF_NAME:_CT_OFF_NAME + 64].split(b'\0')[0]
        if b'AMD' in name.upper():
            p = struct.unpack_from('<f', buf, _CT_OFF_POWER)[0]
            if 1.0 <= p <= 500.0:
                power = p
        return temp, power

    def close(self) -> None:
        pass                                # 无长驻句柄


class SysData:
    """一次采集的快照"""
    __slots__ = (
        'cpu_pct', 'cpu_freq', 'cpu_temp', 'cpu_power',
        'mem_pct', 'mem_used', 'mem_total',
        'gpu_pct', 'gpu_mem_used', 'gpu_mem_total', 'gpu_temp', 'gpu_power',
        'net_down', 'net_up',
    )

    def __init__(self):
        self.cpu_pct: float = 0.0
        self.cpu_freq: float = 0.0
        self.cpu_temp: float | None = None      # Core Temp 未运行则为 None
        self.cpu_power: float | None = None
        self.mem_pct: float = 0.0
        self.mem_used: int = 0
        self.mem_total: int = 0
        self.gpu_pct: float | None = None
        self.gpu_mem_used: int | None = None
        self.gpu_mem_total: int | None = None
        self.gpu_temp: float | None = None
        self.gpu_power: float | None = None
        self.net_down: float = 0.0
        self.net_up: float = 0.0

    @property
    def has_gpu(self) -> bool:
        return self.gpu_pct is not None


class DataCollector(QObject):
    """后台轮询系统数据"""
    data_ready = Signal(SysData)

    def __init__(self):
        super().__init__()

        # PDH 查询（CPU 实际频率）
        self._pdh_query: int | None = None
        self._pdh_counter: int | None = None
        if PDH_OK:
            try:
                self._pdh_query = win32pdh.OpenQuery()
                path = r'\Processor Information(0,_Total)\Actual Frequency'
                self._pdh_counter = win32pdh.AddCounter(self._pdh_query, path)
                win32pdh.CollectQueryData(self._pdh_query)
            except Exception as e:
                log(f"PDH 初始化失败: {e}")

        if NVML_OK:
            try:
                nvmlInit()
                log("NVML 初始化成功")
            except Exception as e:
                log(f"NVML 初始化失败: {e}")

        # 网速追踪
        self._prev_net = psutil.net_io_counters()
        self._prev_net_at = datetime.now()

        # CPU 温度来源（Core Temp 共享内存，未运行时读到 None）
        self._ct = CoreTempReader()
        self._ct_logged = False

        # 区块可见标志（由 MonitorWidget 控制，隐藏时跳过对应采集）
        self.gpu_enabled = True
        self.cpu_enabled = True

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._collect)
        self._timer.start(UPDATE_MS)

    # ── 采集 ──

    def _collect(self) -> None:
        d = SysData()
        try:
            d.cpu_pct  = psutil.cpu_percent(interval=0)
            # CPU 频率（PDH Actual Frequency，比 psutil.cpu_freq 更接近真实值）
            if self._pdh_counter is not None:
                try:
                    win32pdh.CollectQueryData(self._pdh_query)
                    val = win32pdh.GetFormattedCounterValue(
                        self._pdh_counter, win32pdh.PDH_FMT_DOUBLE)
                    d.cpu_freq = val[1]
                except Exception:
                    pass

            mem = psutil.virtual_memory()
            d.mem_pct   = mem.percent
            d.mem_used  = mem.used
            d.mem_total = mem.total

            if self.cpu_enabled:
                d.cpu_temp, d.cpu_power = self._ct.read()
                if d.cpu_temp is not None and not self._ct_logged:
                    self._ct_logged = True
                    log(f"Core Temp 已连接（CPU {d.cpu_temp:.0f}°C {d.cpu_power or 0:.0f}W）")

            self._fill_gpu(d)
            self._fill_net(d)
            self.data_ready.emit(d)
        except Exception:
            log("ERROR _collect:\n" + traceback.format_exc())

    def _fill_net(self, d: SysData) -> None:
        cur = psutil.net_io_counters()
        now = datetime.now()
        elapsed = (now - self._prev_net_at).total_seconds()
        if elapsed > 0:
            d.net_down = (cur.bytes_recv - self._prev_net.bytes_recv) / elapsed
            d.net_up   = (cur.bytes_sent - self._prev_net.bytes_sent) / elapsed
        self._prev_net = cur
        self._prev_net_at = now

    # ── GPU ──

    def _fill_gpu(self, d: SysData) -> None:
        if not NVML_OK or not self.gpu_enabled:
            return
        try:
            count = nvmlDeviceGetCount()
            if count == 0:
                return
            h = nvmlDeviceGetHandleByIndex(0)
            u = nvmlDeviceGetUtilizationRates(h)
            m = nvmlDeviceGetMemoryInfo(h)
            d.gpu_pct        = u.gpu
            d.gpu_mem_used   = m.used
            d.gpu_mem_total  = m.total
            try:
                d.gpu_temp = nvmlDeviceGetTemperature(h, NVML_TEMPERATURE_GPU)
            except Exception:
                d.gpu_temp = None
            try:
                d.gpu_power = nvmlDeviceGetPowerUsage(h) / 1000.0   # mW -> W
            except Exception:
                d.gpu_power = None
        except Exception:
            pass

    def cleanup(self) -> None:
        if self._pdh_query is not None:
            try: win32pdh.CloseQuery(self._pdh_query)
            except Exception: pass
        self._ct.close()
        if NVML_OK:
            try: nvmlShutdown()
            except Exception: pass


# ════════════════════════════════════════════════════════════════════
#  自定义进度条
# ════════════════════════════════════════════════════════════════════

class AnimatedBar(QWidget):
    """进度条 — 纯色填充，无渐变，零动画开销"""

    HEIGHT = 4

    def __init__(self, base_color: QColor, parent=None):
        super().__init__(parent)
        self._color = base_color
        self._pct = 0.0
        self.setFixedHeight(self.HEIGHT)

    def set_pct(self, val: float) -> None:
        val = max(0.0, min(100.0, val))
        if val == self._pct:
            return
        self._pct = val
        self.update()

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        fill = int(w * self._pct / 100.0)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(COLOR_BAR_BG)
        p.drawRoundedRect(0, 0, w, h, 2, 2)

        if self._pct < 60:
            c = self._color
        elif self._pct < 85:
            c = QColor(255, 185, 50)
        else:
            c = QColor(235, 75, 75)
        p.setBrush(c)
        p.drawRoundedRect(0, 0, fill, h, 2, 2)
        p.end()


# ════════════════════════════════════════════════════════════════════
#  指标区块
# ════════════════════════════════════════════════════════════════════

class Section(QFrame):
    """一个指标的显示单元（CPU / GPU / MEM）"""

    def __init__(self, label: str, color: QColor,
                 bands: tuple[int, int, int] = TEMP_BANDS_CPU, parent=None):
        super().__init__(parent)
        self._color = color
        self._bands = bands          # 温度配色阈值
        self._build_ui(label)

    def _build_ui(self, label: str) -> None:
        self.setFixedHeight(WIDGET_HEIGHT)

        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(10, 3, 10, 3)
        vbox.setSpacing(1)

        # ── 顶行：标签 百分比 ──
        top = QHBoxLayout()
        top.setSpacing(6)

        self.lbl_title = QLabel(label)
        self.lbl_title.setFont(QFont(FONT_UI, 9, QFont.Weight.Bold))
        self.lbl_title.setStyleSheet(f"color: {self._color.name()};")
        top.addWidget(self.lbl_title)

        top.addStretch()

        self.lbl_power = QLabel("")
        self.lbl_power.setFont(QFont(FONT_DATA, 8))
        self.lbl_power.setStyleSheet(f"color: {COLOR_DIM.name()};")
        self.lbl_power.hide()              # 无功耗源时不占位
        top.addWidget(self.lbl_power)

        self.lbl_pct = QLabel("0%")
        self.lbl_pct.setFont(QFont(FONT_DATA, 9))
        self.lbl_pct.setStyleSheet(f"color: {COLOR_TEXT.name()};")
        top.addWidget(self.lbl_pct)

        vbox.addLayout(top)

        # ── 进度条 ──
        self.bar = AnimatedBar(self._color)
        vbox.addWidget(self.bar)

        # ── 底行：详情（左） + 温度（右，正对上方百分比） ──
        bottom = QHBoxLayout()
        bottom.setSpacing(6)

        self.lbl_info = QLabel("")
        self.lbl_info.setFont(QFont(FONT_DATA, 7))
        self.lbl_info.setStyleSheet(f"color: {COLOR_DIM.name()};")
        bottom.addWidget(self.lbl_info)

        bottom.addStretch()

        self.lbl_temp = QLabel("")
        self.lbl_temp.setFont(QFont(FONT_DATA, 8, QFont.Weight.Bold))
        self.lbl_temp.hide()               # 无温度源时不占位
        bottom.addWidget(self.lbl_temp)

        vbox.addLayout(bottom)

        self._temp_txt = ""
        self._power_txt = ""

    def refresh(self, pct: float, info: str = "",
                bar_pct: float | None = None,
                temp: float | None = None,
                power: float | None = None) -> None:
        self.lbl_pct.setText(f"{pct:.0f}%")
        self.bar.set_pct(bar_pct if bar_pct is not None else pct)
        self.lbl_info.setText(info)
        self._set_temp(temp)
        self._set_power(power)

    def _set_temp(self, temp: float | None) -> None:
        """温度不可用（Core Temp 未运行）时隐藏，避免留下空位"""
        if temp is None:
            if self._temp_txt:
                self._temp_txt = ""
                self.lbl_temp.hide()
            return

        # 先取整再配色：显示的数字和颜色判定用同一个整数，边界不会出现
        # 「显示 65°C 却是灰色」这种不一致
        t = round(temp)
        txt = f"{t}°C"
        if txt != self._temp_txt:
            self._temp_txt = txt
            self.lbl_temp.setText(txt)
            self.lbl_temp.setStyleSheet(f"color: {_temp_color(t, self._bands).name()};")
        if not self.lbl_temp.isVisibleTo(self):
            self.lbl_temp.show()

    def _set_power(self, power: float | None) -> None:
        """功耗取不到时隐藏（Core Temp 未运行 / 非 AMD / 驱动不支持）"""
        if power is None:
            if self._power_txt:
                self._power_txt = ""
                self.lbl_power.hide()
            return

        txt = f"{power:.0f}W"
        if txt != self._power_txt:
            self._power_txt = txt
            self.lbl_power.setText(txt)
        if not self.lbl_power.isVisibleTo(self):
            self.lbl_power.show()


# ════════════════════════════════════════════════════════════════════
#  网速显示
# ════════════════════════════════════════════════════════════════════

def _fmt_speed(bps: float) -> str:
    if bps >= 1024**3:
        return f"{bps/1024**3:.1f} GB/s"
    elif bps >= 1024**2:
        return f"{bps/1024**2:.1f} MB/s"
    elif bps >= 1024:
        return f"{bps/1024:.0f} KB/s"
    else:
        return f"{bps:.0f} B/s"


class NetSection(QFrame):
    """网速显示（仅数字，无进度条）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(WIDGET_HEIGHT)

        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(10, 3, 10, 3)
        vbox.setSpacing(1)

        # 下载
        d = QHBoxLayout()
        d.setSpacing(4)
        dl = QLabel("D")
        dl.setFont(QFont(FONT_DATA, 8, QFont.Weight.Bold))
        dl.setStyleSheet(f"color: {COLOR_NET.name()};")
        self.lbl_down = QLabel("0 KB/s")
        self.lbl_down.setFont(QFont(FONT_DATA, 8))
        self.lbl_down.setStyleSheet(f"color: {COLOR_TEXT.name()};")
        d.addWidget(dl)
        d.addWidget(self.lbl_down)
        d.addStretch()
        vbox.addLayout(d)

        # 上传
        u = QHBoxLayout()
        u.setSpacing(4)
        ul = QLabel("U")
        ul.setFont(QFont(FONT_DATA, 8, QFont.Weight.Bold))
        ul.setStyleSheet(f"color: {COLOR_DIM.name()};")
        self.lbl_up = QLabel("0 KB/s")
        self.lbl_up.setFont(QFont(FONT_DATA, 8))
        self.lbl_up.setStyleSheet(f"color: {COLOR_DIM.name()};")
        u.addWidget(ul)
        u.addWidget(self.lbl_up)
        u.addStretch()
        vbox.addLayout(u)

    def refresh(self, down_bps: float, up_bps: float) -> None:
        self.lbl_down.setText(_fmt_speed(down_bps))
        self.lbl_up.setText(_fmt_speed(up_bps))


# ════════════════════════════════════════════════════════════════════
#  主窗口
# ════════════════════════════════════════════════════════════════════

class MonitorWidget(QWidget):
    """无边框、置顶、半透明的悬浮监控栏"""

    def __init__(self):
        super().__init__(None)
        self._drag      = False
        self._drag_from = QPoint()
        self._menu_open = False
        self._last_data: SysData | None = None
        self._load_config()            # 先读配置：置顶状态、温度/功耗开关

        self._build_window()
        self._build_ui()
        self._start_collector()

        # ── 置顶兜底：低频重申，对付其他第三方置顶窗口 ──
        self._topmost_timer = QTimer(self)
        self._topmost_timer.timeout.connect(self._force_topmost)
        self._topmost_timer.start(5000)

        # ── 选择性显示 ──
        # key -> (section, trailing_separator_or_None)
        self._blocks: dict[str, tuple[QFrame, QFrame | None]] = {
            'cpu': (self.sec_cpu, self._sep1),
            'gpu': (self.sec_gpu, self._sep2),
            'mem': (self.sec_mem, self._sep3),
            'net': (self.sec_net, None),
        }
        self._apply_visibility()
        # 同步区块可见性到采集器（隐藏时跳过对应采集）
        self._collector.gpu_enabled = self._visible_keys.get('gpu', True)
        self._collector.cpu_enabled = self._visible_keys.get('cpu', True)
        self._dock()

    # ── 窗口属性 ──

    def _build_window(self) -> None:
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool  # Tool: 无任务栏图标
        if self._always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedHeight(WIDGET_HEIGHT)
        self.setMouseTracking(True)

    # ── UI ──

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.sec_cpu = Section("CPU", COLOR_CPU, TEMP_BANDS_CPU)
        self.sec_gpu = Section("GPU", COLOR_GPU, TEMP_BANDS_GPU)
        self.sec_mem = Section("MEM", COLOR_MEM)
        self.sec_net = NetSection()

        def _sep() -> QFrame:
            s = QFrame()
            s.setFrameShape(QFrame.Shape.VLine)
            s.setStyleSheet(f"background:transparent;border-left:1px solid {COLOR_BORDER.name()};")
            s.setFixedWidth(1)
            return s

        layout.addWidget(self.sec_cpu, 1)
        self._sep1 = _sep(); layout.addWidget(self._sep1)
        layout.addWidget(self.sec_gpu, 1)
        self._sep2 = _sep(); layout.addWidget(self._sep2)
        layout.addWidget(self.sec_mem, 1)
        self._sep3 = _sep(); layout.addWidget(self._sep3)
        layout.addWidget(self.sec_net, 1)

    # ── 数据 ──

    def _start_collector(self) -> None:
        self._collector = DataCollector()
        self._collector.data_ready.connect(self._on_data)

    def _on_data(self, d: SysData) -> None:
        try:
            self._last_data = d
            show_temp  = self._show.get('temp', True)
            show_power = self._show.get('power', False)
            freq = d.cpu_freq
            self.sec_cpu.refresh(d.cpu_pct,
                                 info=f"{freq/1000:.2f} GHz" if freq else "",
                                 temp=d.cpu_temp if show_temp else None,
                                 power=d.cpu_power if show_power else None)

            if d.has_gpu and d.gpu_mem_total > 0:
                used_gb = d.gpu_mem_used / 1024**3
                tot_gb  = d.gpu_mem_total / 1024**3
                vram_pct = d.gpu_mem_used / d.gpu_mem_total * 100
                info    = f"{used_gb:.1f}/{tot_gb:.1f} GB"   # 省掉 "VRAM" 前缀，给温度留位置
                self.sec_gpu.refresh(d.gpu_pct, info, bar_pct=vram_pct,
                                     temp=d.gpu_temp if show_temp else None,
                                     power=d.gpu_power if show_power else None)

            used_gb = d.mem_used / 1024**3
            tot_gb  = d.mem_total / 1024**3
            self.sec_mem.refresh(d.mem_pct,
                                 f"{used_gb:.1f}/{tot_gb:.1f} GB")

            self.sec_net.refresh(d.net_down, d.net_up)
        except Exception:
            log("ERROR _on_data:\n" + traceback.format_exc())

    # ── 停靠：屏幕底部居中 ──

    def _dock(self) -> None:
        scr = self.screen() or QApplication.primaryScreen()
        if not scr:
            return
        # 确保布局反映最新的可见性变化
        self.layout().activate()
        avail = scr.availableGeometry()

        visible_n = sum(1 for v in self._visible_keys.values() if v)
        ideal = 120 * visible_n
        natural = self.sizeHint().width()
        w = min(max(natural, ideal), avail.width() - 40)
        x = avail.x()
        y = avail.y() + avail.height() - WIDGET_HEIGHT

        self.setGeometry(x, y, w, WIDGET_HEIGHT)
        self._force_topmost()

    # ── 强制置顶 ──

    def _force_topmost(self) -> None:
        """通过 Windows API 强制将窗口置于 Z 序最顶层"""
        if self._menu_open or not self._always_on_top:
            return
        try:
            hwnd = int(self.winId())
            _user32.SetWindowPos(
                wintypes.HWND(hwnd),
                wintypes.HWND(HWND_TOPMOST),
                0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
            )
        except Exception:
            pass

    def _set_always_on_top(self, on: bool) -> None:
        """切换置顶状态；关闭时把窗口移出 TOPMOST 层"""
        self._always_on_top = on
        try:
            hwnd = int(self.winId())
            _user32.SetWindowPos(
                wintypes.HWND(hwnd),
                wintypes.HWND(HWND_TOPMOST if on else HWND_NOTOPMOST),
                0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
            )
        except Exception:
            pass
        self._save_config()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._force_topmost()

    # ── 选择性显示 ──

    def _load_config(self) -> None:
        """读取 hoverbar.json；首次运行：区块全开、只显示温度、不置顶"""
        defaults = {'cpu': True, 'gpu': True, 'mem': True, 'net': True}
        show_defaults = {'temp': True, 'power': False}
        cfg = {}
        try:
            if os.path.exists(CONFIG_FILE):
                # utf-8-sig：容忍记事本 / PowerShell 5.1 写出的 BOM，否则会解析失败退回默认值
                with open(CONFIG_FILE, 'r', encoding='utf-8-sig') as f:
                    cfg = json.load(f)
        except Exception:
            cfg = {}

        saved = cfg.get('visible', {}) if isinstance(cfg, dict) else {}
        self._visible_keys = {k: saved.get(k, defaults[k]) for k in defaults}

        saved_show = cfg.get('show', {}) if isinstance(cfg, dict) else {}
        self._show = {k: saved_show.get(k, show_defaults[k]) for k in show_defaults}

        self._always_on_top = bool(cfg.get('always_on_top', False))

    def _save_config(self) -> None:
        """写入 hoverbar.json（防抖：300ms 内多次调用只写一次）"""
        try:
            if hasattr(self, '_save_timer') and self._save_timer.isActive():
                self._save_timer.stop()
            else:
                self._save_timer = QTimer(self)
                self._save_timer.setSingleShot(True)
                self._save_timer.timeout.connect(self._flush_config)
            self._save_timer.start(300)
        except Exception:
            pass

    def _flush_config(self) -> None:
        """实际写入磁盘"""
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump({
                    'visible': self._visible_keys,
                    'show': self._show,
                    'always_on_top': self._always_on_top,
                }, f, indent=2)
        except Exception:
            pass

    def _apply_visibility(self) -> None:
        """根据 _visible_keys 显示/隐藏区块和分隔符"""
        keys = list(self._blocks.keys())
        for key in keys:
            section, _ = self._blocks[key]
            section.setVisible(self._visible_keys.get(key, True))

        for i, key in enumerate(keys):
            _, sep = self._blocks[key]
            if not sep:
                continue
            this_visible = self._visible_keys.get(key, True)
            next_visible = False
            for j in range(i + 1, len(keys)):
                if self._visible_keys.get(keys[j], True):
                    next_visible = True
                    break
            sep.setVisible(this_visible and next_visible)

        self._save_config()

    # ── 背景绘制 ──

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect().adjusted(1, 1, -1, -1)

        # 填充
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(COLOR_BG)
        p.drawRoundedRect(r, 8, 8)

        # 边框
        p.setPen(QPen(COLOR_BORDER, 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r, 8, 8)
        p.end()

    # ── 鼠标交互 ──

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag      = True
            self._drag_from = e.globalPosition().toPoint()
            e.accept()
        elif e.button() == Qt.MouseButton.RightButton:
            self._menu(e.globalPosition().toPoint())

    def mouseMoveEvent(self, e) -> None:
        if self._drag and e.buttons() == Qt.MouseButton.LeftButton:
            delta = e.globalPosition().toPoint() - self._drag_from
            self.move(self.pos() + delta)
            self._drag_from = e.globalPosition().toPoint()
            e.accept()

    def mouseReleaseEvent(self, e) -> None:
        self._drag = False
        e.accept()

    def mouseDoubleClickEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._dock()
            self._force_topmost()

    # ── 右键菜单 ──

    def _menu(self, pos) -> None:
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background: #2d2d30; border:1px solid #3e3e42;
                padding:4px; color:#ccc;
            }
            QMenu::item {
                padding:6px 24px; border-radius:4px;
            }
            QMenu::item:selected { background:#094771; }
            QMenu::separator {
                height:1px; background:#3e3e42;
                margin:4px 8px;
            }
        """)
        a_dock = QAction("Dock to Bottom", self)
        a_dock.triggered.connect(self._dock)
        menu.addAction(a_dock)

        a_top = QAction("Always on Top", self)
        a_top.setCheckable(True)
        a_top.setChecked(self._always_on_top)
        a_top.toggled.connect(self._set_always_on_top)
        menu.addAction(a_top)

        menu.addSeparator()

        # 选择性显示 — 每项可勾选
        section_info = [
            ('cpu', 'CPU'),
            ('gpu', 'GPU'),
            ('mem', 'Memory'),
            ('net', 'Network'),
        ]
        for key, label in section_info:
            a = QAction(label, self)
            a.setCheckable(True)
            a.setChecked(self._visible_keys.get(key, True))
            a._section_key = key
            a.toggled.connect(self._on_section_toggled)
            menu.addAction(a)

        menu.addSeparator()

        # 温度 / 功耗开关（默认只显示温度）
        show_info = [
            ('temp', 'Temperature'),
            ('power', 'Power'),
        ]
        for key, label in show_info:
            a = QAction(label, self)
            a.setCheckable(True)
            a.setChecked(self._show.get(key, False))
            a._show_key = key
            a.toggled.connect(self._on_show_toggled)
            menu.addAction(a)

        menu.addSeparator()

        a_quit = QAction("Quit", self)
        a_quit.triggered.connect(self._quit)
        menu.addAction(a_quit)

        # 菜单窗口也置于 TOPMOST 层，防止被自身或其他置顶窗口盖住
        menu.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)

        self._menu_open = True
        try:
            menu.exec(pos)
        finally:
            self._menu_open = False

    def _on_section_toggled(self, visible: bool) -> None:
        """切换某个区块的可见性，禁止全部隐藏"""
        action = self.sender()
        key = action._section_key
        if not visible:
            # 至少保留一个可见区块
            remaining = sum(1 for k, v in self._visible_keys.items() if v and k != key)
            if remaining == 0:
                action.setChecked(True)
                return
        self._visible_keys[key] = visible
        # 同步可见性到采集器（隐藏时跳过 NVML / Core Temp 查询）
        if key == 'gpu':
            self._collector.gpu_enabled = visible
        elif key == 'cpu':
            self._collector.cpu_enabled = visible
        self._apply_visibility()
        self._dock()

    def _on_show_toggled(self, visible: bool) -> None:
        """切换温度 / 功耗的显示，立即重排避免留空"""
        action = self.sender()
        self._show[action._show_key] = visible
        self._save_config()
        # 立刻按新开关重画一次（下一次采集也会自然生效）
        self._refresh_now()

    def _refresh_now(self) -> None:
        """用上一帧数据立即刷新（不额外采集）"""
        if self._last_data is not None:
            self._on_data(self._last_data)

    def _quit(self) -> None:
        log("退出")
        self._collector.cleanup()
        QApplication.quit()


# ════════════════════════════════════════════════════════════════════
#  启动
# ════════════════════════════════════════════════════════════════════

def _compute_widget_height() -> int:
    """按任务栏实际高度确定控件高度，适配不同分辨率/缩放。"""
    scr = QApplication.primaryScreen()
    if not scr:
        return 44
    tb = scr.geometry().height() - scr.availableGeometry().height()
    if tb <= 0:
        return 44
    return max(32, min(tb, 60))


def main() -> int:
    global WIDGET_HEIGHT
    log(f"启动 — NVML={NVML_OK}")
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    app.setQuitOnLastWindowClosed(False)

    WIDGET_HEIGHT = _compute_widget_height()
    log(f"任务栏自适应高度 = {WIDGET_HEIGHT}px")

    w = MonitorWidget()
    w.show()
    log("窗口已显示")

    rc = app.exec()
    log(f"事件循环退出 code={rc}")
    return rc


if __name__ == '__main__':
    sys.exit(main())
