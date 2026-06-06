#!/usr/bin/env python3
"""
HoverBar - 桌面系统监控条
实时显示 CPU/GPU/内存/网速，悬停在任务栏上方
半透明无边框，支持拖拽和右键菜单
"""

import sys
import os
import json
import traceback
from datetime import datetime
from typing import Optional

# ── Qt ──
from PySide6.QtWidgets import (
    QApplication, QWidget, QHBoxLayout, QVBoxLayout,
    QLabel, QFrame, QMenu,
)
from PySide6.QtCore import Qt, QTimer, Signal, QObject, QPoint
from PySide6.QtGui import (
    QFont, QColor, QPainter, QPen, QLinearGradient,
    QAction, QFontDatabase,
)

# ── Windows API（强制置顶） ──
import ctypes
from ctypes import wintypes

HWND_TOPMOST   = -1
SWP_NOMOVE     = 0x0002
SWP_NOSIZE     = 0x0001
SWP_NOACTIVATE = 0x0010

_user32 = ctypes.windll.user32

# ── 系统监控 ──
import psutil

# ── NVIDIA GPU ──
try:
    from pynvml import (
        nvmlInit, nvmlDeviceGetHandleByIndex,
        nvmlDeviceGetUtilizationRates, nvmlDeviceGetMemoryInfo,
        nvmlDeviceGetTemperature, NVML_TEMPERATURE_GPU,
        nvmlShutdown, nvmlDeviceGetCount,
    )
    NVML_OK = True
except Exception:
    NVML_OK = False

# ── WMI（CPU温度） ──
try:
    import wmi
    WMI_OK = True
except Exception:
    WMI_OK = False


# ════════════════════════════════════════════════════════════════════
#  常量
# ════════════════════════════════════════════════════════════════════

UPDATE_MS = 1500          # 刷新间隔（毫秒）
WIDGET_HEIGHT = 44        # 控件高度

COLOR_BG     = QColor(28, 28, 30, 210)
COLOR_BORDER = QColor(60, 60, 65, 80)
COLOR_TEXT   = QColor(220, 220, 225)
COLOR_DIM    = QColor(140, 140, 150)
COLOR_BAR_BG = QColor(60, 60, 65, 120)
COLOR_CPU    = QColor(0,  120, 212)   # 蓝
COLOR_GPU    = QColor(197, 48, 48)    # 红
COLOR_MEM    = QColor(16, 124, 16)    # 绿

FONT_NAME = "Segoe UI"

# ── 路径（兼容 PyInstaller 打包） ──
if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = APP_DIR
LOG_FILE = os.path.join(LOG_DIR, "hoverbar.log")
CONFIG_FILE = os.path.join(LOG_DIR, "hoverbar.json")

def log(msg: str) -> None:
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%H:%M:%S}] {msg}\n")
    except Exception:
        pass


# ════════════════════════════════════════════════════════════════════
#  数据采集
# ════════════════════════════════════════════════════════════════════

class SysData:
    """一次采集的快照"""
    __slots__ = (
        'cpu_pct', 'cpu_temp',
        'mem_pct', 'mem_used', 'mem_total',
        'gpu_pct', 'gpu_mem_used', 'gpu_mem_total', 'gpu_temp',
        'net_down', 'net_up',
    )

    def __init__(self):
        self.cpu_pct: float = 0.0
        self.cpu_temp: Optional[float] = None
        self.mem_pct: float = 0.0
        self.mem_used: int = 0
        self.mem_total: int = 0
        self.gpu_pct: Optional[float] = None
        self.gpu_mem_used: Optional[int] = None
        self.gpu_mem_total: Optional[int] = None
        self.gpu_temp: Optional[float] = None
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
        self._wmi_conn: Optional['wmi.WMI'] = None          # root\wmi
        self._wmi_lhm: Optional['wmi.WMI'] = None            # LibreHardwareMonitor
        self._wmi_ohm: Optional['wmi.WMI'] = None            # OpenHardwareMonitor
        self._wmi_perf: Optional['wmi.WMI'] = None           # root\cimv2 (性能计数器)
        self._have_cpu_temp = False
        self._cpu_temp_source: Optional[int] = None  # 缓存成功源（1-4），下次优先

        if NVML_OK:
            try:
                nvmlInit()
                log("NVML 初始化成功")
            except Exception as e:
                log(f"NVML 初始化失败: {e}")

        if WMI_OK:
            ns_list = [
                ("root\\wmi",              "_wmi_conn"),
                ("root\\LibreHardwareMonitor", "_wmi_lhm"),
                ("root\\OpenHardwareMonitor",  "_wmi_ohm"),
            ]
            for ns, attr in ns_list:
                try:
                    setattr(self, attr, wmi.WMI(namespace=ns))
                    log(f"WMI 连接成功: {ns}")
                except Exception as e:
                    log(f"WMI 连接失败 {ns}: {e}")

            # 额外试 root\cimv2 下 Win32_PerfFormattedData_Counters_ThermalZoneInformation
            try:
                cimv2 = wmi.WMI(namespace="root\\cimv2")
                # 仅测试该查询是否可用
                test = cimv2.query("SELECT * FROM Win32_PerfFormattedData_Counters_ThermalZoneInformation")
                if len(test) > 0:
                    self._wmi_perf = cimv2
                    log("WMI 连接成功: root\\cimv2 (ThermalZoneInformation)")
                else:
                    log("WMI root\\cimv2 ThermalZoneInformation: 无数据")
            except Exception as e:
                log(f"WMI root\\cimv2 ThermalZoneInformation 失败: {e}")

            self._have_cpu_temp = bool(self._wmi_conn or self._wmi_lhm
                                       or self._wmi_ohm or self._wmi_perf)
            log(f"CPU 温度检测: {'可用' if self._have_cpu_temp else '不可用'}")

        # 网速追踪
        self._prev_net = psutil.net_io_counters()
        self._prev_net_at = datetime.now()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._collect)
        self._timer.start(UPDATE_MS)

    # ── 采集 ──

    def _collect(self) -> None:
        d = SysData()
        try:
            d.cpu_pct  = psutil.cpu_percent(interval=0)
            d.cpu_temp = self._cpu_temp()

            mem = psutil.virtual_memory()
            d.mem_pct   = mem.percent
            d.mem_used  = mem.used
            d.mem_total = mem.total

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

    # ── CPU 温度 ──

    def _cpu_temp(self) -> Optional[float]:
        # 若有上次成功的缓存源，优先尝试（短路）
        if self._cpu_temp_source:
            result = self._try_temp_source(self._cpu_temp_source)
            if result is not None:
                return result
            self._cpu_temp_source = None

        # 遍历所有源，第一个命中则缓存
        for idx in (1, 2, 3, 4):
            result = self._try_temp_source(idx)
            if result is not None:
                self._cpu_temp_source = idx
                return result
        return None

    def _try_temp_source(self, idx: int) -> Optional[float]:
        """尝试单个温度数据源，成功返回温度，失败返回 None"""
        if idx == 1 and self._wmi_conn:
            try:
                zones = self._wmi_conn.MSAcpi_ThermalZoneTemperature()
                if zones:
                    k = zones[0].CurrentTemperature
                    if k and k > 0:
                        return round(k / 10 - 273.15, 1)
            except Exception:
                pass
        elif idx == 2 and self._wmi_perf:
            try:
                for tz in self._wmi_perf.query(
                    "SELECT * FROM Win32_PerfFormattedData_Counters_ThermalZoneInformation"
                ):
                    if hasattr(tz, 'Temperature') and tz.Temperature:
                        return round(tz.Temperature / 10, 1)
            except Exception:
                pass
        elif idx == 3 and self._wmi_lhm:
            try:
                for s in self._wmi_lhm.Sensor():
                    if s.SensorType == 'Temperature' and 'CPU' in str(s.Name):
                        return round(float(s.Value), 1)
            except Exception:
                pass
        elif idx == 4 and self._wmi_ohm:
            try:
                for s in self._wmi_ohm.Sensor():
                    if s.SensorType == 'Temperature' and 'CPU' in str(s.Name):
                        return round(float(s.Value), 1)
            except Exception:
                pass
        return None

    # ── GPU ──

    def _fill_gpu(self, d: SysData) -> None:
        if not NVML_OK:
            return
        try:
            count = nvmlDeviceGetCount()
            if count == 0:
                return
            h = nvmlDeviceGetHandleByIndex(0)
            u = nvmlDeviceGetUtilizationRates(h)
            m = nvmlDeviceGetMemoryInfo(h)
            t = nvmlDeviceGetTemperature(h, NVML_TEMPERATURE_GPU)
            d.gpu_pct        = u.gpu
            d.gpu_mem_used   = m.used
            d.gpu_mem_total  = m.total
            d.gpu_temp       = t
        except Exception:
            pass

    def cleanup(self) -> None:
        if NVML_OK:
            try: nvmlShutdown()
            except Exception: pass


# ════════════════════════════════════════════════════════════════════
#  自定义进度条
# ════════════════════════════════════════════════════════════════════

class BarWidget(QWidget):
    """彩色渐变进度条"""

    HEIGHT = 4

    def __init__(self, base_color: QColor, parent=None):
        super().__init__(parent)
        self._pct: float = 0.0
        self._color = base_color
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

        # 背景
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(COLOR_BAR_BG)
        p.drawRoundedRect(0, 0, w, h, 2, 2)

        # 填充 — 按使用率变色
        if self._pct < 60:
            c = self._color
        elif self._pct < 85:
            c = QColor(255, 180, 0)
        else:
            c = QColor(220, 50, 50)
        grd = QLinearGradient(0, 0, fill, 0)
        grd.setColorAt(0, c)
        grd.setColorAt(1, c.lighter(130))
        p.setBrush(grd)
        p.drawRoundedRect(0, 0, fill, h, 2, 2)
        p.end()


# ════════════════════════════════════════════════════════════════════
#  指标区块
# ════════════════════════════════════════════════════════════════════

class Section(QFrame):
    """一个指标的显示单元（CPU / GPU / MEM）"""

    def __init__(self, label: str, icon: str, color: QColor, parent=None):
        super().__init__(parent)
        self._color = color
        self._build_ui(label, icon)

    def _build_ui(self, label: str, icon: str) -> None:
        self.setFixedHeight(WIDGET_HEIGHT)

        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(10, 3, 10, 3)
        vbox.setSpacing(1)

        # ── 顶行：图标 百分比 温度 ──
        top = QHBoxLayout()
        top.setSpacing(5)

        self.lbl_title = QLabel(f"{icon} {label}")
        self.lbl_title.setFont(QFont(FONT_NAME, 9, QFont.Weight.Bold))
        self.lbl_title.setStyleSheet(f"color: {self._color.name()};")
        top.addWidget(self.lbl_title)

        top.addStretch()

        self.lbl_pct = QLabel("0%")
        self.lbl_pct.setFont(QFont(FONT_NAME, 9))
        self.lbl_pct.setStyleSheet(f"color: {COLOR_TEXT.name()};")
        top.addWidget(self.lbl_pct)

        self.lbl_temp = QLabel("")
        self.lbl_temp.setFont(QFont(FONT_NAME, 8))
        self.lbl_temp.setStyleSheet(f"color: {COLOR_DIM.name()};")
        top.addWidget(self.lbl_temp)

        vbox.addLayout(top)

        # ── 进度条 ──
        self.bar = BarWidget(self._color)
        vbox.addWidget(self.bar)

        # ── 底行（VRAM / 内存详情） ──
        self.lbl_info = QLabel("")
        self.lbl_info.setFont(QFont(FONT_NAME, 7))
        self.lbl_info.setStyleSheet(f"color: {COLOR_DIM.name()};")
        vbox.addWidget(self.lbl_info)

    def refresh(self, pct: float, temp: float | None = None,
                info: str = "", bar_pct: float | None = None) -> None:
        self.lbl_pct.setText(f"{pct:.0f}%")
        self.bar.set_pct(bar_pct if bar_pct is not None else pct)
        self.lbl_temp.setText(f"🌡 {temp:.0f}°C" if temp is not None else "")
        self.lbl_info.setText(info)


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

    COLOR_NET = QColor(180, 130, 220)   # 紫色

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(WIDGET_HEIGHT)

        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(10, 3, 10, 3)
        vbox.setSpacing(1)

        # ⬇ 下载
        d = QHBoxLayout()
        d.setSpacing(4)
        dl = QLabel("⬇")
        dl.setFont(QFont(FONT_NAME, 8))
        dl.setStyleSheet(f"color: {self.COLOR_NET.name()};")
        self.lbl_down = QLabel("0 KB/s")
        self.lbl_down.setFont(QFont(FONT_NAME, 8))
        self.lbl_down.setStyleSheet(f"color: {COLOR_TEXT.name()};")
        d.addWidget(dl)
        d.addWidget(self.lbl_down)
        d.addStretch()
        vbox.addLayout(d)

        # ⬆ 上传
        u = QHBoxLayout()
        u.setSpacing(4)
        ul = QLabel("⬆")
        ul.setFont(QFont(FONT_NAME, 8))
        ul.setStyleSheet(f"color: {COLOR_DIM.name()};")
        self.lbl_up = QLabel("0 KB/s")
        self.lbl_up.setFont(QFont(FONT_NAME, 8))
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

        self._build_window()
        self._build_ui()
        self._start_collector()

        # ── 选择性显示 ──
        # key -> (section, trailing_separator_or_None)
        self._blocks: dict[str, tuple[QFrame, QFrame | None]] = {
            'cpu': (self.sec_cpu, self._sep1),
            'gpu': (self.sec_gpu, self._sep2),
            'mem': (self.sec_mem, self._sep3),
            'net': (self.sec_net, None),
        }
        self._load_config()
        self._apply_visibility()
        self._dock()

    # ── 窗口属性 ──

    def _build_window(self) -> None:
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool               # 无任务栏图标
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedHeight(WIDGET_HEIGHT)
        self.setMouseTracking(True)

    # ── UI ──

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.sec_cpu = Section("CPU",  "🖥", COLOR_CPU)
        self.sec_gpu = Section("GPU",  "🎮", COLOR_GPU)
        self.sec_mem = Section("MEM", "📊", COLOR_MEM)
        self.sec_net = NetSection()

        def _sep() -> QFrame:
            s = QFrame()
            s.setFrameShape(QFrame.Shape.VLine)
            s.setStyleSheet(f"background:{COLOR_BORDER.name()};max-width:1px;")
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
            self.sec_cpu.refresh(d.cpu_pct, None)

            if d.has_gpu:
                used_gb = d.gpu_mem_used / 1024**3
                tot_gb  = d.gpu_mem_total / 1024**3
                vram_pct = d.gpu_mem_used / d.gpu_mem_total * 100
                info    = f"VRAM {used_gb:.1f}/{tot_gb:.1f} GB"
                self.sec_gpu.refresh(d.gpu_pct, d.gpu_temp, info,
                                     bar_pct=vram_pct)

            used_gb = d.mem_used / 1024**3
            tot_gb  = d.mem_total / 1024**3
            self.sec_mem.refresh(d.mem_pct, None,
                                 f"{used_gb:.1f}/{tot_gb:.1f} GB")

            self.sec_net.refresh(d.net_down, d.net_up)

            # 每次数据刷新顺带重新声明置顶
            self._force_topmost()
        except Exception:
            log("ERROR _on_data:\n" + traceback.format_exc())

    # ── 停靠：屏幕底部居中 ──

    def _dock(self) -> None:
        scr = QApplication.primaryScreen()
        if not scr:
            return
        # 确保布局反映最新的可见性变化
        self.layout().activate()
        avail = scr.availableGeometry()
        full  = scr.geometry()

        # 估算任务栏高度（0 表示任务栏在侧面／隐藏）
        taskbar_h = full.height() - avail.height()
        taskbar_b = full.width()  - avail.width()

        visible_n = sum(1 for v in self._visible_keys.values() if v)
        ideal = 180 * visible_n
        natural = self.sizeHint().width()
        w = min(max(natural, ideal), avail.width() - 40)
        x = avail.x() + (avail.width() - w) // 2
        y = avail.y() + avail.height() - WIDGET_HEIGHT

        # 如果任务栏在底部，贴上去
        if taskbar_h > 0 and taskbar_b == 0:
            y -= taskbar_h

        self.setGeometry(x, y, w, WIDGET_HEIGHT)
        self._force_topmost()

    # ── 强制置顶 ──

    def _force_topmost(self) -> None:
        """通过 Windows API 强制将窗口置于 Z 序最顶层"""
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

    def showEvent(self, event) -> None:
        """窗口显示时立即置顶"""
        super().showEvent(event)
        self._force_topmost()

    # ── 选择性显示 ──

    def _load_config(self) -> None:
        """读取 hoverbar.json，首次运行默认全部可见"""
        defaults = {'cpu': True, 'gpu': True, 'mem': True, 'net': True}
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    saved = json.load(f).get('visible', {})
                self._visible_keys = {k: saved.get(k, defaults[k]) for k in defaults}
            else:
                self._visible_keys = dict(defaults)
        except Exception:
            self._visible_keys = dict(defaults)

    def _save_config(self) -> None:
        """写入 hoverbar.json"""
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump({'visible': self._visible_keys}, f, indent=2)
        except Exception:
            pass

    def _apply_visibility(self) -> None:
        """根据 _visible_keys 显示/隐藏区块和分隔符"""
        keys = list(self._blocks.keys())
        for i, key in enumerate(keys):
            section, sep = self._blocks[key]
            visible = self._visible_keys.get(key, True)
            section.setVisible(visible)
            if sep:
                # 分隔符只在两个相邻区块都可见时才显示
                next_key = keys[i + 1] if i + 1 < len(keys) else None
                next_visible = self._visible_keys.get(next_key, True) if next_key else False
                sep.setVisible(visible and next_visible)
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
        a_dock = QAction("📌  复位到底部", self)
        a_dock.triggered.connect(self._dock)
        menu.addAction(a_dock)

        menu.addSeparator()

        # 选择性显示 — 每项可勾选
        section_info = [
            ('cpu', '🖥  CPU'),
            ('gpu', '🎮  GPU'),
            ('mem', '📊  内存'),
            ('net', '🌐  网速'),
        ]
        for key, label in section_info:
            a = QAction(label, self)
            a.setCheckable(True)
            a.setChecked(self._visible_keys.get(key, True))
            a._section_key = key
            a.toggled.connect(self._on_section_toggled)
            menu.addAction(a)

        menu.addSeparator()

        a_quit = QAction("❌  退出", self)
        a_quit.triggered.connect(self._quit)
        menu.addAction(a_quit)

        menu.exec(pos)

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
        self._apply_visibility()
        self._dock()

    def _quit(self) -> None:
        log("退出")
        self._collector.cleanup()
        QApplication.quit()


# ════════════════════════════════════════════════════════════════════
#  启动
# ════════════════════════════════════════════════════════════════════

def main() -> int:
    log(f"启动 — NVML={NVML_OK} WMI={WMI_OK}")
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    app.setQuitOnLastWindowClosed(False)

    w = MonitorWidget()
    w.show()
    log("窗口已显示")

    rc = app.exec()
    log(f"事件循环退出 code={rc}")
    return rc


if __name__ == '__main__':
    sys.exit(main())
