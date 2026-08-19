"""STATCOM 上位机主窗口（V0x0104 保护阈值配置与 DSP/CPLD 独立故障版）。

遵循《单相STATCOM调试上位机设计与通信协议规范 V1.0》、《单相STATCOM上位机 V0x0104 保护阈值功能修改交底书》与最新直流电压标定系数。
"""

from __future__ import annotations

import os
import time
from collections import deque

os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")

import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent, QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

try:
    from serial.tools.list_ports import comports
except Exception:  # noqa: BLE001
    comports = None

from communication.serial_worker import FrameResult, SerialWorker
from protocol import register_model as rm
from storage.logger import DataLogger
from ui.theme import build_stylesheet
from ui.widgets import LedIndicator, StatCard, format_uptime, state_color

HISTORY_LEN = 300
POLL_DEFAULT_MS = 100


class MaintenanceConfirmDialog(QDialog):
    """维护动作关键字二次确认弹窗。"""

    def __init__(self, parent, title: str, prompt: str, required_keyword: str, warning_text: str):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(500)
        self.required_keyword = required_keyword

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(20, 20, 20, 20)

        warn_label = QLabel(f"<b>⚠️ 高危维护操作警告：</b><br>{warning_text}")
        warn_label.setStyleSheet("color:#e5484d;font-size:13px;line-height:1.4;")
        warn_label.setWordWrap(True)
        layout.addWidget(warn_label)

        prompt_label = QLabel(f"{prompt}<br>必须精确输入文本：<b style='color:#f5b83d;font-family:Consolas,monospace;font-size:14px;'>{required_keyword}</b>")
        prompt_label.setStyleSheet("font-size:13px;")
        prompt_label.setWordWrap(True)
        layout.addWidget(prompt_label)

        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText(f"请输入 {required_keyword}")
        self.input_edit.setStyleSheet(
            "font-family:Consolas, monospace;font-size:14px;padding:8px;"
            "background-color:#0b1017;color:#ffffff;border:1px solid #3b82f6;border-radius:6px;"
        )
        layout.addWidget(self.input_edit)

        self.btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.btn_box.button(QDialogButtonBox.StandardButton.Ok).setText("确认执行")
        self.btn_box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.btn_box.accepted.connect(self._validate_and_accept)
        self.btn_box.rejected.connect(self.reject)
        layout.addWidget(self.btn_box)

    def _validate_and_accept(self):
        if self.input_edit.text().strip() == self.required_keyword:
            self.accept()
        else:
            QMessageBox.critical(self, "输入不匹配", f"输入的确认文本与 '{self.required_keyword}' 不一致，操作已取消！")


class MainWindow(QMainWindow):
    """STATCOM 上位机监控系统主窗口。"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("单相 STATCOM 调试监控系统 V1.04 (保护阈值配置 & 29 Regs 版)")
        self.resize(1440, 930)
        self.setMinimumSize(1200, 760)

        # 状态变量
        self._connected = False
        self._worker: SerialWorker | None = None
        self._logger = DataLogger(output_dir="data_logs")
        self._latest_raw: list[int] = []
        self._latest_data: dict = {}
        self._latest_protocol_version: int = 0x0000
        self._dsp_request_cmd: int = 0  # 0: STOP, 1: START
        self._maintenance_in_progress: bool = False
        self._maintenance_action_name: str = ""
        self._pending_fault_clear_verify: bool = False
        self._pending_adc_cal_verify: bool = False

        # VDC 标定参数 (可配置)
        self._vdc_gain: float = rm.DEFAULT_VDC_GAIN  # 默认 0.28568177
        self._vdc_zero: float = rm.DEFAULT_VDC_ZERO  # 默认 0.0

        # 通信诊断统计
        self._tx_count = 0
        self._rx_count = 0
        self._crc_err_count = 0
        self._timeout_count = 0
        self._exception_count = 0
        self._latency_sum = 0.0
        self._latency_samples = 0

        # 5 秒增量滑动窗口缓存
        self._err_history: deque[tuple[float, dict[str, int]]] = deque()

        # 趋势曲线缓存
        self._t_buffer: deque[float] = deque(maxlen=HISTORY_LEN)
        self._iac_buffer: deque[float] = deque(maxlen=HISTORY_LEN)
        self._vdc_buffer: deque[float] = deque(maxlen=HISTORY_LEN)
        self._vgrid_buffer: deque[float] = deque(maxlen=HISTORY_LEN)
        self._pll_buffer: deque[float] = deque(maxlen=HISTORY_LEN)
        self._plot_paused = False

        self._build_ui()
        self._apply_theme()

        # 定时器
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(POLL_DEFAULT_MS)
        self._poll_timer.timeout.connect(self._on_poll_tick)
        self._poll_timer.start()

        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start()

        self._refresh_ports()

    # ------------------------------------------------------------------
    # UI 搭建
    # ------------------------------------------------------------------
    def _apply_theme(self):
        self.setStyleSheet(build_stylesheet())

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(10)

        outer.addLayout(self._build_top_bar())
        outer.addWidget(self._build_tabs(), 1)
        outer.addLayout(self._build_bottom_bar())

    def _panel(self, title: str) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName("Panel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        lbl = QLabel(title)
        lbl.setObjectName("PanelTitle")
        lbl.setStyleSheet("color:#f1f5f9;font-size:14px;font-weight:700;")
        layout.addWidget(lbl)
        return frame, layout

    def _build_top_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(10)

        logo = QLabel("⚡ STATCOM 监控控制台")
        logo.setObjectName("Brand")
        logo.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        bar.addWidget(logo)

        bar.addSpacing(16)

        bar.addWidget(QLabel("串口:"))
        self._port_combo = QComboBox()
        self._port_combo.setMinimumWidth(230)
        bar.addWidget(self._port_combo)

        btn_scan = QPushButton("扫描")
        btn_scan.clicked.connect(self._refresh_ports)
        bar.addWidget(btn_scan)

        bar.addWidget(QLabel("波特率:"))
        self._baud_combo = QComboBox()
        self._baud_combo.addItems(["115200", "9600", "19200", "38400", "57600"])
        bar.addWidget(self._baud_combo)

        bar.addWidget(QLabel("从站:"))
        self._slave_spin = QComboBox()
        self._slave_spin.addItems(["2", "1", "3", "4"])
        bar.addWidget(self._slave_spin)

        self._connect_btn = QPushButton("连接")
        self._connect_btn.setObjectName("PrimaryBtn")
        self._connect_btn.setMinimumWidth(100)
        self._connect_btn.clicked.connect(self._toggle_connect)
        bar.addWidget(self._connect_btn)

        self._conn_led = LedIndicator("未连接")
        bar.addWidget(self._conn_led)

        bar.addStretch(1)
        return bar

    def _build_tabs(self) -> QTabWidget:
        tabs = QTabWidget()
        tabs.setObjectName("MainTabs")
        tabs.addTab(self._build_dashboard_tab(), "📊 实时总览")
        tabs.addTab(self._build_registers_tab(), "📋 寄存器全表")
        tabs.addTab(self._build_trends_tab(), "📈 趋势波形")
        tabs.addTab(self._build_faults_tab(), "⚠️ 故障与保护")
        tabs.addTab(self._build_diag_tab(), "🔍 通信诊断")
        return tabs

    # --- 标签 1: 实时总览 ---
    def _build_dashboard_tab(self) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(4, 8, 4, 4)
        layout.setSpacing(12)

        # 左侧：状态指示
        left_box = QWidget()
        left_box.setFixedWidth(310)
        left_layout = QVBoxLayout(left_box)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)

        # 调试状态卡片
        state_frame, state_layout = self._panel("DSP 系统状态")
        self._state_value = QLabel("未连接")
        self._state_value.setObjectName("StateValue")
        self._state_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        state_layout.addWidget(self._state_value)

        self._adc_zero_label = QLabel("ADC 校零：--")
        self._adc_zero_label.setObjectName("Caption")
        self._adc_zero_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        state_layout.addWidget(self._adc_zero_label)

        self._cmd_status_box = QLabel("命令状态：未发送")
        self._cmd_status_box.setStyleSheet("color:#6c8299;font-size:12px;")
        self._cmd_status_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        state_layout.addWidget(self._cmd_status_box)

        left_layout.addWidget(state_frame)

        # CPLD 状态
        cpld_frame, cpld_layout = self._panel("CPLD 硬件状态")
        self._status_leds: dict[str, LedIndicator] = {}
        for key, label in (
            ("link_online", "DSP-CPLD 链路在线"),
            ("adc_valid", "CPLD ADC 采样有效"),
            ("temperature_valid", "温度脉冲计数有效"),
            ("fault_any", "存在硬件锁存故障"),
            ("pwm_healthy", "PWM 监测健康"),
        ):
            led = LedIndicator(label)
            cpld_layout.addWidget(led)
            self._status_leds[key] = led

        left_layout.addWidget(cpld_frame)

        # 锁相环状态
        pll_frame, pll_layout = self._panel("SOGI-PLL 锁相状态")
        self._pll_locked_led = LedIndicator("PLL 锁相环已锁定")
        self._pll_valid_led = LedIndicator("正半波信号幅值有效")
        pll_layout.addWidget(self._pll_locked_led)
        pll_layout.addWidget(self._pll_valid_led)
        left_layout.addWidget(pll_frame)

        left_layout.addStretch(1)
        layout.addWidget(left_box)

        # 右侧：遥测卡片、保护阈值、标定与事件跟踪
        right_box = QWidget()
        right_layout = QVBoxLayout(right_box)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)

        # 遥测大字卡片网格
        cards_frame, cards_layout = self._panel("核心遥测数据 (工程物理量与原始码)")
        cards_grid = QGridLayout()
        cards_grid.setSpacing(10)

        self._card_grid_v = StatCard("电网电压 (正半波)", "V", "ADCINA0 原始码: --")
        self._card_iac = StatCard("交流电流 Iac", "A", "瞬时: -- | 零点: --")
        self._card_vdc = StatCard("直流母线电压 Vdc", "V", f"K={self._vdc_gain:.4f} | 原始码: --")
        self._card_pll_f = StatCard("电网基波频率 PLL", "Hz", "锁相状态: --")
        self._card_temp = StatCard("IGBT 脉冲频率", "Hz", "计数: -- count/100ms | 门限: 5000")
        self._card_adc_count = StatCard("ADC 采样计数", "次", "协议版本: --")

        cards_grid.addWidget(self._card_grid_v, 0, 0)
        cards_grid.addWidget(self._card_iac, 0, 1)
        cards_grid.addWidget(self._card_vdc, 0, 2)
        cards_grid.addWidget(self._card_pll_f, 1, 0)
        cards_grid.addWidget(self._card_temp, 1, 1)
        cards_grid.addWidget(self._card_adc_count, 1, 2)
        cards_layout.addLayout(cards_grid)
        right_layout.addWidget(cards_frame)

        # V0105 保护阈值按需配置面板
        prot_frame, prot_layout = self._panel("🛡️ 保护阈值配置 (0x1000 ~ 0x1002 原始码协议)")
        prot_strip = QHBoxLayout()
        prot_strip.setSpacing(10)

        prot_strip.addWidget(QLabel("Vdc 软件过压 (V):"))
        self._spin_prot_vdc = QDoubleSpinBox()
        self._spin_prot_vdc.setRange(0.1, rm.vdc_ov_raw_to_eng(rm.PROT_VDC_OV_MAX_RAW_V105, self._vdc_gain, self._vdc_zero))
        self._spin_prot_vdc.setDecimals(1)
        self._spin_prot_vdc.setSingleStep(1.0)
        self._spin_prot_vdc.setValue(rm.vdc_ov_raw_to_eng(rm.PROT_VDC_OV_DEFAULT_RAW_V105, self._vdc_gain, self._vdc_zero))
        self._spin_prot_vdc.setMinimumWidth(80)
        prot_strip.addWidget(self._spin_prot_vdc)
        self._lbl_vdc_raw_hint = QLabel("(3410 count)")
        self._lbl_vdc_raw_hint.setStyleSheet("color:#38bdf8;font-size:11px;font-family:Consolas,monospace;")
        prot_strip.addWidget(self._lbl_vdc_raw_hint)

        prot_strip.addWidget(QLabel("电网峰值过压 (V):"))
        self._spin_prot_grid = QDoubleSpinBox()
        self._spin_prot_grid.setRange(0.1, rm.grid_peak_ov_raw_to_eng(rm.PROT_GRID_PEAK_OV_MAX_RAW_V105, rm.DEFAULT_K_GRID))
        self._spin_prot_grid.setDecimals(1)
        self._spin_prot_grid.setSingleStep(1.0)
        self._spin_prot_grid.setValue(rm.grid_peak_ov_raw_to_eng(rm.PROT_GRID_PEAK_OV_DEFAULT_RAW_V105, rm.DEFAULT_K_GRID))
        self._spin_prot_grid.setMinimumWidth(80)
        prot_strip.addWidget(self._spin_prot_grid)
        self._lbl_grid_raw_hint = QLabel("(1313 count)")
        self._lbl_grid_raw_hint.setStyleSheet("color:#38bdf8;font-size:11px;font-family:Consolas,monospace;")
        prot_strip.addWidget(self._lbl_grid_raw_hint)

        prot_strip.addWidget(QLabel("交流瞬时过流 (A):"))
        self._spin_prot_iac = QDoubleSpinBox()
        self._spin_prot_iac.setRange(0.01, rm.iac_oc_raw_to_eng(rm.PROT_IAC_OC_MAX_RAW_V105, rm.DEFAULT_K_IAC))
        self._spin_prot_iac.setDecimals(2)
        self._spin_prot_iac.setSingleStep(0.1)
        self._spin_prot_iac.setValue(rm.iac_oc_raw_to_eng(rm.PROT_IAC_OC_DEFAULT_RAW_V105, rm.DEFAULT_K_IAC))
        self._spin_prot_iac.setMinimumWidth(80)
        prot_strip.addWidget(self._spin_prot_iac)
        self._lbl_iac_raw_hint = QLabel("(51 count)")
        self._lbl_iac_raw_hint.setStyleSheet("color:#38bdf8;font-size:11px;font-family:Consolas,monospace;")
        prot_strip.addWidget(self._lbl_iac_raw_hint)

        self._spin_prot_vdc.valueChanged.connect(self._update_prot_spin_hints)
        self._spin_prot_grid.valueChanged.connect(self._update_prot_spin_hints)
        self._spin_prot_iac.valueChanged.connect(self._update_prot_spin_hints)

        self._btn_read_prot = QPushButton("📥 读取阈值 (FC03)")
        self._btn_read_prot.setToolTip("发送 FC03 请求读取 0x1000 起始 3 个保护阈值原始码")
        self._btn_read_prot.clicked.connect(self._on_read_thresholds_clicked)
        prot_strip.addWidget(self._btn_read_prot)

        self._btn_write_prot = QPushButton("📤 写入阈值 (FC06)")
        self._btn_write_prot.setToolTip("发送 FC06 依次写入 0x1000~0x1002 保护阈值原始码（仅限 STOP 状态）")
        self._btn_write_prot.clicked.connect(self._on_write_thresholds_clicked)
        prot_strip.addWidget(self._btn_write_prot)

        prot_strip.addStretch(1)
        prot_layout.addLayout(prot_strip)

        self._prot_status_label = QLabel("⚠️ 说明：DSP/CPLD 直接比较原始码；仅 STOP 可写，只允许调低不可调高，复位恢复默认值，当前未掉电保存。")
        self._prot_status_label.setStyleSheet("color:#38bdf8;font-family:Consolas,monospace;font-size:11px;")
        prot_layout.addWidget(self._prot_status_label)
        right_layout.addWidget(prot_frame)

        # VDC 快速标定设置面板
        cal_frame, cal_layout = self._panel("⚡ 直流电压标定配置 (Vdc Zero & Gain Calibration)")
        cal_strip = QHBoxLayout()
        cal_strip.setSpacing(10)

        cal_strip.addWidget(QLabel("增益 K (V/count):"))
        self._spin_vdc_gain = QDoubleSpinBox()
        self._spin_vdc_gain.setRange(0.0001, 10.0)
        self._spin_vdc_gain.setDecimals(4)
        self._spin_vdc_gain.setSingleStep(0.01)
        self._spin_vdc_gain.setValue(self._vdc_gain)
        self._spin_vdc_gain.setMinimumWidth(95)
        cal_strip.addWidget(self._spin_vdc_gain)

        cal_strip.addWidget(QLabel("设置零点值 (Zero):"))
        self._spin_vdc_zero = QDoubleSpinBox()
        self._spin_vdc_zero.setRange(0.0, 4095.0)
        self._spin_vdc_zero.setDecimals(1)
        self._spin_vdc_zero.setSingleStep(1.0)
        self._spin_vdc_zero.setValue(self._vdc_zero)
        self._spin_vdc_zero.setMinimumWidth(85)
        cal_strip.addWidget(self._spin_vdc_zero)

        btn_apply_cal = QPushButton("💾 应用标定")
        btn_apply_cal.setToolTip("按公式 Vdc = (Count - Zero) * K 计算直流电压")
        btn_apply_cal.clicked.connect(self._on_apply_vdc_calibration)
        cal_strip.addWidget(btn_apply_cal)

        btn_zero_current = QPushButton("🎯 取当前读数为零点")
        btn_zero_current.setToolTip("将当前实测平均码直接作为零点值填入并减去")
        btn_zero_current.clicked.connect(self._on_zero_from_current)
        cal_strip.addWidget(btn_zero_current)

        btn_preset_new = QPushButton("⚡ 恢复理论值 (K=0.2857, Zero=0)")
        btn_preset_new.setToolTip("新电路理论比例：K = 5*4604.6 / (4095*8.2*2.4) ≈ 0.2857 V/count，零点=0")
        btn_preset_new.clicked.connect(lambda: self._set_cal_preset(0.2857, 0.0))
        cal_strip.addWidget(btn_preset_new)

        btn_preset_zero4 = QPushButton("⚡ 设零点为4 (Zero=4)")
        btn_preset_zero4.setToolTip("空载未接高压时若有4左右的偏置，设Zero=4以减去偏置")
        btn_preset_zero4.clicked.connect(lambda: self._set_cal_preset(0.2857, 4.0))
        cal_strip.addWidget(btn_preset_zero4)

        cal_strip.addStretch(1)
        cal_layout.addLayout(cal_strip)

        self._cal_formula_preview = QLabel(
            f"当前计算公式: Vdc = (Count - {self._vdc_zero:.1f}) × {self._vdc_gain:.4f} V  |  "
            "说明：未接高压时若采样读数不是0（如为4左右），填入该零点值即可自动减去消除零漂。"
        )
        self._cal_formula_preview.setStyleSheet("color:#38bdf8;font-family:Consolas,monospace;font-size:11px;")
        cal_layout.addWidget(self._cal_formula_preview)
        right_layout.addWidget(cal_frame)

        # 运行日志简视
        log_frame, log_layout = self._panel("实时事件跟踪")
        self._dash_log = QPlainTextEdit()
        self._dash_log.setObjectName("LogView")
        self._dash_log.setReadOnly(True)
        self._dash_log.setMaximumBlockCount(300)
        log_layout.addWidget(self._dash_log)
        right_layout.addWidget(log_frame, 1)

        layout.addWidget(right_box, 1)
        return container

    # --- 标签 2: 寄存器全表 (独立专用界面) ---
    def _build_registers_tab(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 8, 4, 4)
        layout.setSpacing(8)

        # 说明与操作栏
        top_bar = QHBoxLayout()
        hint = QLabel("<b>FC04 全部 29 个输入寄存器详细映射与实时监控表 (0x0000 ~ 0x001C)</b>")
        hint.setStyleSheet("font-size:14px;color:#f8fafc;")
        top_bar.addWidget(hint)

        top_bar.addStretch(1)
        self._reg_proto_label = QLabel("当前协议：未读取")
        self._reg_proto_label.setStyleSheet("color:#38bdf8;font-size:13px;font-weight:600;")
        top_bar.addWidget(self._reg_proto_label)
        layout.addLayout(top_bar)

        # 全表
        self._full_reg_table = QTableWidget(29, 8)
        self._full_reg_table.setStyleSheet(
            "QTableWidget {"
            "  background-color: #0b1118;"
            "  alternate-background-color: #121a24;"
            "  gridline-color: #1a2533;"
            "  color: #f1f5f9;"
            "  border: 1px solid #223040;"
            "  border-radius: 6px;"
            "}"
            "QHeaderView::section {"
            "  background-color: #080c12;"
            "  color: #94a3b8;"
            "  border: none;"
            "  border-bottom: 1px solid #223040;"
            "  border-right: 1px solid #1a2533;"
            "  padding: 6px;"
            "  font-weight: 600;"
            "}"
            "QTableWidget::item {"
            "  padding: 4px 8px;"
            "  border-bottom: 1px solid #141d28;"
            "  color: #f1f5f9;"
            "}"
            "QTableWidget::item:selected {"
            "  background-color: #1e3a5f;"
            "  color: #ffffff;"
            "}"
        )
        headers = ["序号", "寄存器地址", "信号名称", "所属分类", "16位HEX", "10位DEC", "物理量 / 状态解释", "详细说明"]
        self._full_reg_table.setHorizontalHeaderLabels(headers)
        self._full_reg_table.verticalHeader().setVisible(False)
        self._full_reg_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._full_reg_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._full_reg_table.setAlternatingRowColors(True)

        # 列宽设置
        self._full_reg_table.setColumnWidth(0, 50)   # 序号
        self._full_reg_table.setColumnWidth(1, 90)   # 地址
        self._full_reg_table.setColumnWidth(2, 210)  # 信号名称
        self._full_reg_table.setColumnWidth(3, 95)   # 分类
        self._full_reg_table.setColumnWidth(4, 90)   # HEX
        self._full_reg_table.setColumnWidth(5, 90)   # DEC
        self._full_reg_table.setColumnWidth(6, 260)  # 物理量解释
        self._full_reg_table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)  # 详细说明

        for row, item in enumerate(rm.REGISTER_DEFINITIONS):
            self._full_reg_table.setRowHeight(row, 30)

            seq_item = QTableWidgetItem(f"{row:02d}")
            seq_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            addr_item = QTableWidgetItem(f"0x{item['addr']:04X}")
            addr_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            addr_item.setForeground(QColor("#38bdf8"))

            name_item = QTableWidgetItem(item["name"])
            name_item.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))

            cat_item = QTableWidgetItem(item["category"])
            cat_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            cat_item.setForeground(QColor("#94a3b8"))

            hex_item = QTableWidgetItem("--")
            hex_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            hex_item.setForeground(QColor("#a78bfa"))
            hex_item.setFont(QFont("Consolas", 10))

            dec_item = QTableWidgetItem("--")
            dec_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            dec_item.setFont(QFont("Consolas", 10))

            interp_item = QTableWidgetItem("--")
            interp_item.setForeground(QColor("#34d399"))
            interp_item.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))

            desc_item = QTableWidgetItem(item["desc"])
            desc_item.setForeground(QColor("#94a3b8"))

            self._full_reg_table.setItem(row, 0, seq_item)
            self._full_reg_table.setItem(row, 1, addr_item)
            self._full_reg_table.setItem(row, 2, name_item)
            self._full_reg_table.setItem(row, 3, cat_item)
            self._full_reg_table.setItem(row, 4, hex_item)
            self._full_reg_table.setItem(row, 5, dec_item)
            self._full_reg_table.setItem(row, 6, interp_item)
            self._full_reg_table.setItem(row, 7, desc_item)

        layout.addWidget(self._full_reg_table, 1)
        return container

    # --- 标签 3: 趋势波形 ---
    def _build_trends_tab(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 8, 4, 4)
        layout.setSpacing(8)

        # 波形控制栏
        ctrl_bar = QHBoxLayout()
        self._chk_iac = QCheckBox("Iac 交流电流 (A)")
        self._chk_iac.setChecked(True)
        self._chk_iac.setStyleSheet("color:#38bdf8;font-weight:600;")
        ctrl_bar.addWidget(self._chk_iac)

        self._chk_vdc = QCheckBox("Vdc 直流母线电压 (V)")
        self._chk_vdc.setChecked(True)
        self._chk_vdc.setStyleSheet("color:#f5b83d;font-weight:600;")
        ctrl_bar.addWidget(self._chk_vdc)

        self._chk_vgrid = QCheckBox("Vgrid 电网正半波 (V)")
        self._chk_vgrid.setChecked(True)
        self._chk_vgrid.setStyleSheet("color:#34d399;font-weight:600;")
        ctrl_bar.addWidget(self._chk_vgrid)

        self._chk_pll = QCheckBox("PLL 频率 (Hz)")
        self._chk_pll.setChecked(False)
        self._chk_pll.setStyleSheet("color:#a78bfa;font-weight:600;")
        ctrl_bar.addWidget(self._chk_pll)

        ctrl_bar.addStretch(1)

        self._btn_pause = QPushButton("⏸ 暂停刷新")
        self._btn_pause.clicked.connect(self._toggle_plot_pause)
        ctrl_bar.addWidget(self._btn_pause)

        self._btn_clear_plot = QPushButton("🗑 清空波形")
        self._btn_clear_plot.clicked.connect(self._clear_plot)
        ctrl_bar.addWidget(self._btn_clear_plot)

        layout.addLayout(ctrl_bar)

        # PyQtGraph 波形图
        self._plot_widget = pg.PlotWidget()
        self._plot_widget.setBackground("#0b1016")
        self._plot_widget.showGrid(x=True, y=True, alpha=0.25)
        self._plot_widget.setLabel("left", "工程物理量幅值")
        self._plot_widget.setLabel("bottom", "采样点序号")
        self._plot_widget.addLegend(offset=(10, 10))

        self._curve_iac = self._plot_widget.plot(
            pen=pg.mkPen("#38bdf8", width=2), name="Iac (A)"
        )
        self._curve_vdc = self._plot_widget.plot(
            pen=pg.mkPen("#f5b83d", width=2), name="Vdc (V)"
        )
        self._curve_vgrid = self._plot_widget.plot(
            pen=pg.mkPen("#34d399", width=2), name="Vgrid (V)"
        )
        self._curve_pll = self._plot_widget.plot(
            pen=pg.mkPen("#a78bfa", width=2), name="PLL (Hz)"
        )

        layout.addWidget(self._plot_widget, 1)
        return container

    # --- 标签 4: 故障与保护 (V0104 独立拆分 DSP 与 CPLD) ---
    def _build_faults_tab(self) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(4, 8, 4, 4)
        layout.setSpacing(12)

        # 左侧：DSP 保护锁存与 CPLD 故障独立监控
        left_box = QWidget()
        left_layout = QVBoxLayout(left_box)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)

        # 1. DSP 保护锁存位域 (0x000C)
        dsp_prot_frame, dsp_prot_layout = self._panel("🛡️ DSP 保护锁存状态 (0x000C)")
        self._dsp_prot_table = QTableWidget(5, 3)
        self._dsp_prot_table.setStyleSheet(
            "QTableWidget { background-color: #0b1118; alternate-background-color: #121a24; gridline-color: #1a2533; color: #f1f5f9; border: 1px solid #223040; border-radius: 6px; }"
            "QHeaderView::section { background-color: #080c12; color: #94a3b8; border: none; border-bottom: 1px solid #223040; border-right: 1px solid #1a2533; padding: 6px; font-weight: 600; }"
            "QTableWidget::item { padding: 4px 8px; border-bottom: 1px solid #141d28; color: #f1f5f9; }"
        )
        self._dsp_prot_table.setHorizontalHeaderLabels(["位", "保护名称", "当前状态"])
        self._dsp_prot_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._dsp_prot_table.setColumnWidth(0, 65)
        self._dsp_prot_table.setColumnWidth(2, 130)
        self._dsp_prot_table.verticalHeader().setVisible(False)
        self._dsp_prot_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._dsp_prot_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        dsp_prot_layout.addWidget(self._dsp_prot_table)

        self._dsp_prot_summary_label = QLabel("✅ DSP 保护正常")
        self._dsp_prot_summary_label.setStyleSheet("color:#34d399;font-size:12px;font-weight:600;")
        dsp_prot_layout.addWidget(self._dsp_prot_summary_label)
        left_layout.addWidget(dsp_prot_frame)

        # 2. CPLD 硬件故障 (0x000D)
        cpld_fault_frame, cpld_fault_layout = self._panel("🛡️ CPLD 硬件故障监控 (0x000D)")
        self._fault_table = QTableWidget(13, 3)
        self._fault_table.setStyleSheet(
            "QTableWidget { background-color: #0b1118; alternate-background-color: #121a24; gridline-color: #1a2533; color: #f1f5f9; border: 1px solid #223040; border-radius: 6px; }"
            "QHeaderView::section { background-color: #080c12; color: #94a3b8; border: none; border-bottom: 1px solid #223040; border-right: 1px solid #1a2533; padding: 6px; font-weight: 600; }"
            "QTableWidget::item { padding: 4px 8px; border-bottom: 1px solid #141d28; color: #f1f5f9; }"
        )
        self._fault_table.setHorizontalHeaderLabels(["位", "故障名称", "当前状态"])
        self._fault_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._fault_table.setColumnWidth(0, 65)
        self._fault_table.setColumnWidth(2, 130)
        self._fault_table.verticalHeader().setVisible(False)
        self._fault_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._fault_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        cpld_fault_layout.addWidget(self._fault_table)

        self._fault_summary_label = QLabel("当前无活动故障")
        self._fault_summary_label.setStyleSheet("color:#34d399;font-size:12px;font-weight:600;")
        cpld_fault_layout.addWidget(self._fault_summary_label)

        # 清除故障按钮
        btn_layout = QHBoxLayout()
        self._btn_clear_fault = QPushButton("🧹 清除 CPLD 锁存故障 (0x0101)")
        self._btn_clear_fault.setEnabled(False)
        self._btn_clear_fault.setToolTip("清除已消除的锁存故障。需处于 STOP 状态且固件支持 (0x0101+)。")
        self._btn_clear_fault.clicked.connect(self._on_clear_fault_clicked)
        btn_layout.addWidget(self._btn_clear_fault)
        cpld_fault_layout.addLayout(btn_layout)

        # 高级维护操作折叠区
        maint_group = QFrame()
        maint_group.setStyleSheet("QFrame { background-color: #0d141e; border: 1px solid #1e293b; border-radius: 6px; padding: 6px; }")
        maint_layout = QVBoxLayout(maint_group)
        maint_layout.setSpacing(6)

        maint_title = QLabel("🛠️ 高级维护操作 (受控复位 / ADC 零漂校准)")
        maint_title.setStyleSheet("font-weight: bold; color: #94a3b8; font-size: 12px;")
        maint_layout.addWidget(maint_title)

        maint_btn_row = QHBoxLayout()
        self._btn_reset_cpld = QPushButton("🔄 复位 CPLD (0x0102)")
        self._btn_reset_cpld.setEnabled(False)
        self._btn_reset_cpld.setToolTip("复位 CPLD 通信与内部状态机（需二次输入 'RESET CPLD' 确认）")
        self._btn_reset_cpld.clicked.connect(self._on_reset_cpld_clicked)
        maint_btn_row.addWidget(self._btn_reset_cpld)

        self._btn_reset_dsp = QPushButton("⚡ 复位 DSP (0x0103)")
        self._btn_reset_dsp.setEnabled(False)
        self._btn_reset_dsp.setToolTip("受控软复位 DSP 控制器（需二次输入 'RESET DSP' 确认）")
        self._btn_reset_dsp.clicked.connect(self._on_reset_dsp_clicked)
        maint_btn_row.addWidget(self._btn_reset_dsp)

        self._btn_adc_recal = QPushButton("🎯 ADC 重新校零 (0x0104)")
        self._btn_adc_recal.setEnabled(False)
        self._btn_adc_recal.setToolTip("STOP下保持交流/直流电流传感器零输入，执行1024点零漂校准（约51.2ms）")
        self._btn_adc_recal.clicked.connect(self._on_adc_recalibrate_clicked)
        maint_btn_row.addWidget(self._btn_adc_recal)
        maint_layout.addLayout(maint_btn_row)

        self._maint_ver_hint = QLabel("固件维护支持：未连接")
        self._maint_ver_hint.setStyleSheet("color:#64748b;font-size:12px;")
        maint_layout.addWidget(self._maint_ver_hint)

        self._maint_status_label = QLabel("维护状态：空闲")
        self._maint_status_label.setStyleSheet("color:#94a3b8;font-size:12px;")
        maint_layout.addWidget(self._maint_status_label)

        self._maint_result_label = QLabel("最近结果：--")
        self._maint_result_label.setStyleSheet("color:#94a3b8;font-size:12px;")
        maint_layout.addWidget(self._maint_result_label)

        cpld_fault_layout.addWidget(maint_group)
        left_layout.addWidget(cpld_fault_frame)
        layout.addWidget(left_box, 1)

        # 事件历史日志
        right_frame, right_layout = self._panel("系统事件历史记录")
        self._event_log_view = QPlainTextEdit()
        self._event_log_view.setObjectName("LogView")
        self._event_log_view.setReadOnly(True)
        self._event_log_view.setMaximumBlockCount(1000)
        right_layout.addWidget(self._event_log_view)

        layout.addWidget(right_frame, 1)
        return container

    # --- 标签 5: 四层通信诊断 ---
    def _build_diag_tab(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 8, 4, 4)
        layout.setSpacing(10)

        # A. PC 本地串口统计
        frame_a, layout_a = self._panel("🖥️ A. PC 本地串口统计 (上位机主站 -> DSP SCI-B)")
        grid_a = QGridLayout()
        self._diag_tx = StatCard("发送帧数 TX", "frames")
        self._diag_rx = StatCard("成功接收 RX", "frames")
        self._diag_crc = StatCard("CRC 校验错误", "次")
        self._diag_timeout = StatCard("请求无响应超时", "次")
        self._diag_exception = StatCard("Modbus 异常响应", "次")
        self._diag_latency = StatCard("平均往返延迟", "ms")
        grid_a.addWidget(self._diag_tx, 0, 0)
        grid_a.addWidget(self._diag_rx, 0, 1)
        grid_a.addWidget(self._diag_crc, 0, 2)
        grid_a.addWidget(self._diag_timeout, 1, 0)
        grid_a.addWidget(self._diag_exception, 1, 1)
        grid_a.addWidget(self._diag_latency, 1, 2)
        layout_a.addLayout(grid_a)
        layout.addWidget(frame_a)

        # B/C/D 中间并列网格
        mid_row = QHBoxLayout()
        mid_row.setSpacing(10)

        # B. DSP 端 PC-SCI-B 统计
        frame_b, layout_b = self._panel("⚡ B. DSP 端 PC-SCI-B 统计 (0x0015)")
        self._diag_pc_dsp = StatCard("PC-SCI-B 累计错误", "次", "5s增量: 0")
        layout_b.addWidget(self._diag_pc_dsp)
        self._diag_pc_dsp_hint = QLabel("说明：DSP 接收上位机 SCI-B 请求时捕获的格式/CRC错误累计。")
        self._diag_pc_dsp_hint.setStyleSheet("color:#64748b;font-size:11px;")
        self._diag_pc_dsp_hint.setWordWrap(True)
        layout_b.addWidget(self._diag_pc_dsp_hint)
        mid_row.addWidget(frame_b, 1)

        # C. DSP 接收 CPLD 统计
        frame_c, layout_c = self._panel("📡 C. DSP 接收 CPLD 回复统计 (SCI-A)")
        grid_c = QGridLayout()
        self._diag_dsp_cpld_total = StatCard("综合累计错误 (0014)", "次", "5s增量: 0")
        self._diag_dsp_format = StatCard("SCI-A 硬件格式错误 (001A)", "次", "5s增量: 0")
        self._diag_dsp_overflow = StatCard("SCI-A 接收溢出计数 (001B)", "次", "5s增量: 0")
        self._diag_dsp_timeout = StatCard("等待CPLD回复超时 (001C)", "次", "5s增量: 0")
        grid_c.addWidget(self._diag_dsp_cpld_total, 0, 0)
        grid_c.addWidget(self._diag_dsp_format, 0, 1)
        grid_c.addWidget(self._diag_dsp_overflow, 1, 0)
        grid_c.addWidget(self._diag_dsp_timeout, 1, 1)
        layout_c.addLayout(grid_c)
        mid_row.addWidget(frame_c, 2)

        # D. CPLD 接收 DSP 统计
        frame_d, layout_d = self._panel("🛡️ D. CPLD 接收 DSP 请求统计 (CPLD 从站)")
        grid_d = QGridLayout()
        self._diag_cpld_uart = StatCard("UART 停止位错误 (0017)", "次", "5s增量: 0")
        self._diag_cpld_crc = StatCard("Modbus CRC 错误 (0018)", "次", "5s增量: 0")
        self._diag_cpld_incomplete = StatCard("t3.5 残帧超时 (0019)", "次", "5s增量: 0")
        self._diag_bit10_latch = StatCard("Bit10 锁存状态", "", "未锁存")
        grid_d.addWidget(self._diag_cpld_uart, 0, 0)
        grid_d.addWidget(self._diag_cpld_crc, 0, 1)
        grid_d.addWidget(self._diag_cpld_incomplete, 1, 0)
        grid_d.addWidget(self._diag_bit10_latch, 1, 1)
        layout_d.addLayout(grid_d)
        mid_row.addWidget(frame_d, 2)

        layout.addLayout(mid_row)

        # 原始十六进制收发监视
        frame_hex, layout_hex = self._panel("Modbus RTU 原始报文监控 (最近一次交互)")
        self._tx_hex_edit = QLineEdit()
        self._tx_hex_edit.setReadOnly(True)
        self._tx_hex_edit.setStyleSheet("font-family:Consolas, monospace;background-color:#0b1017;color:#38bdf8;")

        self._rx_hex_edit = QLineEdit()
        self._rx_hex_edit.setReadOnly(True)
        self._rx_hex_edit.setStyleSheet("font-family:Consolas, monospace;background-color:#0b1017;color:#34d399;")

        layout_hex.addWidget(QLabel("TX 发送帧 (主站请求):"))
        layout_hex.addWidget(self._tx_hex_edit)
        layout_hex.addWidget(QLabel("RX 接收帧 (从站响应):"))
        layout_hex.addWidget(self._rx_hex_edit)
        layout.addWidget(frame_hex)

        return container

    # ------------------------------------------------------------------
    # 底部控制与状态栏
    # ------------------------------------------------------------------
    def _build_bottom_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(12)

        self._start_btn = QPushButton("▶ 启动调试 (START)")
        self._start_btn.setObjectName("SuccessBtn")
        self._start_btn.setMinimumHeight(40)
        self._start_btn.setMinimumWidth(160)
        self._start_btn.setEnabled(False)
        self._start_btn.clicked.connect(self._on_start_clicked)
        bar.addWidget(self._start_btn)

        self._stop_btn = QPushButton("⏹ 立即停止 (STOP)")
        self._stop_btn.setObjectName("DangerBtn")
        self._stop_btn.setMinimumHeight(40)
        self._stop_btn.setMinimumWidth(160)
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._on_stop_clicked)
        bar.addWidget(self._stop_btn)

        bar.addSpacing(20)

        self._record_btn = QPushButton("🔴 开始 CSV 记录")
        self._record_btn.clicked.connect(self._toggle_recording)
        bar.addWidget(self._record_btn)

        self._record_status = QLabel("未在记录")
        self._record_status.setStyleSheet("color:#64748b;font-size:12px;")
        bar.addWidget(self._record_status)

        bar.addStretch(1)

        self._uptime_label = QLabel("DSP 运行时间：--")
        self._uptime_label.setStyleSheet("color:#94a3b8;font-size:13px;font-weight:500;")
        bar.addWidget(self._uptime_label)

        self._clock_label = QLabel("--:--:--")
        self._clock_label.setStyleSheet("color:#64748b;font-size:12px;")
        bar.addWidget(self._clock_label)

        return bar

    # ------------------------------------------------------------------
    # 标定与保护阈值参数交互
    # ------------------------------------------------------------------
    def _on_apply_vdc_calibration(self):
        self._vdc_gain = self._spin_vdc_gain.value()
        self._vdc_zero = self._spin_vdc_zero.value()
        self._cal_formula_preview.setText(
            f"当前计算公式: Vdc = (Count - {self._vdc_zero:.1f}) × {self._vdc_gain:.4f} V  |  "
            "说明：未接高压时若采样读数不是0（如为4左右），填入该零点值即可自动减去消除零漂。"
        )
        self._log(f"✅ 直流电压标定已更新：Vdc = (Count - {self._vdc_zero:.1f}) × {self._vdc_gain:.4f} V")
        if self._latest_raw:
            data = rm.parse_input_registers(self._latest_raw, self._vdc_gain, self._vdc_zero)
            self._apply_data(data)
            self._update_registers_table(self._latest_raw, data)

    def _set_cal_preset(self, gain: float, zero: float):
        self._spin_vdc_gain.setValue(gain)
        self._spin_vdc_zero.setValue(zero)
        self._on_apply_vdc_calibration()

    def _on_zero_from_current(self):
        if not self._latest_data:
            self._log("⚠️ 尚未接收到 CPLD 数据，无法读取当前采样码。")
            return
        curr_avg = self._latest_data.get("cpld_vdc_average", 0)
        self._spin_vdc_zero.setValue(float(curr_avg))
        self._on_apply_vdc_calibration()
        self._log(f"🎯 已将当前 CPLD Vdc 平均码 ({curr_avg}) 设为零点并自动减去。")

    def _update_prot_spin_hints(self):
        vdc_raw = rm.vdc_ov_eng_to_raw(self._spin_prot_vdc.value(), self._vdc_gain, self._vdc_zero)
        grid_raw = rm.grid_peak_ov_eng_to_raw(self._spin_prot_grid.value(), rm.DEFAULT_K_GRID)
        iac_raw = rm.iac_oc_eng_to_raw(self._spin_prot_iac.value(), rm.DEFAULT_K_IAC)
        self._lbl_vdc_raw_hint.setText(f"({vdc_raw} count)")
        self._lbl_grid_raw_hint.setText(f"({grid_raw} count)")
        self._lbl_iac_raw_hint.setText(f"({iac_raw} count)")

    def _on_read_thresholds_clicked(self):
        if not self._connected or not self._worker:
            self._log("未连接设备，无法读取保护阈值。")
            return
        self._prot_status_label.setText("正在读取设备保护阈值 (FC03 0x1000 x3)...")
        self._worker.queue_read_thresholds()

    def _on_write_thresholds_clicked(self):
        if not self._connected or not self._worker:
            self._log("未连接设备，无法写入保护阈值。")
            return
        echo_val = self._latest_data.get("cpld_command_echo_raw", 0) if self._latest_data else 0
        if self._dsp_request_cmd != 0 or echo_val != 0:
            QMessageBox.warning(self, "状态联锁拦截", "保护阈值仅允许在 STOP 停机状态下修改！")
            return

        vdc_val = self._spin_prot_vdc.value()
        grid_val = self._spin_prot_grid.value()
        iac_val = self._spin_prot_iac.value()

        vdc_raw = rm.vdc_ov_eng_to_raw(vdc_val, self._vdc_gain, self._vdc_zero)
        grid_raw = rm.grid_peak_ov_eng_to_raw(grid_val, rm.DEFAULT_K_GRID)
        iac_raw = rm.iac_oc_eng_to_raw(iac_val, rm.DEFAULT_K_IAC)

        valid, err_msg = rm.validate_protection_threshold_raw(vdc_raw, grid_raw, iac_raw)
        if not valid:
            QMessageBox.warning(self, "阈值超出安全上限", err_msg)
            return

        # 二次确认弹窗展示物理量与换算后的原始码
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("⚠️ 保护阈值写入二次确认")
        msg_box.setIcon(QMessageBox.Icon.Question)
        msg_box.setText(
            f"<h3>确认向设备写入以下保护阈值？</h3>"
            f"<p>• <b>CPLD Vdc 软件过压 (0x1000)</b>: <b>{vdc_val:.1f} V</b> (原始码 <b>{vdc_raw}</b> count / 上限 3410)<br>"
            f"• <b>DSP 电网峰值过压 (0x1001)</b>: <b>{grid_val:.1f} V</b> (原始码 <b>{grid_raw}</b> count / 上限 1313)<br>"
            f"• <b>DSP 交流瞬时过流 (0x1002)</b>: <b>{iac_val:.2f} A</b> (原始码 <b>{iac_raw}</b> count / 上限 51)</p>"
            f"<p><i>提示：DSP/CPLD 在内部直接比较原始码；写入成功后将自动读回设备实际值进行核对。</i></p>"
        )
        msg_box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        msg_box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if msg_box.exec() != QMessageBox.StandardButton.Yes:
            return

        self._prot_status_label.setText("正在向设备写入保护阈值 (FC06)...")
        self._log(f">>> [用户操作] 写入保护阈值：Vdc={vdc_val:.1f}V ({vdc_raw}), Vgrid={grid_val:.1f}V ({grid_raw}), Iac={iac_val:.2f}A ({iac_raw})")
        self._worker.queue_write_thresholds(vdc_raw, grid_raw, iac_raw)

    def _on_thresholds_read_reply(self, result: FrameResult):
        self._tx_count += 1
        if result.response:
            self._rx_count += 1

        if result.error:
            self._prot_status_label.setText(f"❌ 读保护阈值失败：{result.error}")
            self._log(f"❌ 读取保护阈值失败：{result.error}")
            return

        if result.extra and isinstance(result.extra, (list, tuple)) and len(result.extra) >= 3:
            vdc_raw, grid_raw, iac_raw = result.extra[:3]
            v_vdc = rm.vdc_ov_raw_to_eng(vdc_raw, self._vdc_gain, self._vdc_zero)
            v_grid = rm.grid_peak_ov_raw_to_eng(grid_raw, rm.DEFAULT_K_GRID)
            v_iac = rm.iac_oc_raw_to_eng(iac_raw, rm.DEFAULT_K_IAC)
            self._spin_prot_vdc.blockSignals(True)
            self._spin_prot_grid.blockSignals(True)
            self._spin_prot_iac.blockSignals(True)
            self._spin_prot_vdc.setValue(v_vdc)
            self._spin_prot_grid.setValue(v_grid)
            self._spin_prot_iac.setValue(v_iac)
            self._spin_prot_vdc.blockSignals(False)
            self._spin_prot_grid.blockSignals(False)
            self._spin_prot_iac.blockSignals(False)
            self._update_prot_spin_hints()
            self._prot_status_label.setText(f"✅ 设备实际保护阈值：Vdc={v_vdc:.1f}V ({vdc_raw} count), Vgrid={v_grid:.1f}V ({grid_raw} count), Iac={v_iac:.2f}A ({iac_raw} count)")
            self._log(f"📥 设备保护阈值已同步：Vdc={v_vdc:.1f}V ({vdc_raw}), Vgrid={v_grid:.1f}V ({grid_raw}), Iac={v_iac:.2f}A ({iac_raw})")

    def _on_thresholds_write_reply(self, result: FrameResult):
        self._tx_count += 1
        if result.response:
            self._rx_count += 1

        info = result.extra if isinstance(result.extra, dict) else {}
        if info.get("success"):
            self._log("✅ 保护阈值写入成功，已触发设备读回验证。")
            self._prot_status_label.setText("✅ 保护阈值已成功写入设备并读回确认！")
        else:
            failed_addr = info.get("failed_addr", 0)
            exc = info.get("exception")
            if exc == 0x03:
                msg = "仅 STOP 状态可修改，或原始码超出固件安全范围！"
            elif exc == 0x02:
                msg = "固件版本不支持保护阈值寄存器 (需 0x0104+)！"
            else:
                msg = info.get("error", "写超时或从站未响应")

            self._log(f"❌ 保护阈值写入失败 (0x{failed_addr:04X})：{msg}")
            self._prot_status_label.setText(f"❌ 写入失败：{msg}")
            QMessageBox.warning(self, "保护阈值写入失败", f"寄存器 0x{failed_addr:04X} 写入失败：\n{msg}")

    # ------------------------------------------------------------------
    # 串口连接与生命周期
    # ------------------------------------------------------------------
    def _refresh_ports(self):
        self._port_combo.clear()
        self._port_combo.addItem("虚拟硬件仿真器 (SIMULATOR)")
        if comports:
            ports = comports()
            for p in ports:
                desc = f"{p.device} ({p.description})" if p.description else p.device
                self._port_combo.addItem(p.device)
        self._port_combo.setCurrentIndex(0)

    def _toggle_connect(self):
        if self._connected:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        port = self._port_combo.currentText()
        if not port:
            return
        slave = int(self._slave_spin.currentText())
        baud = int(self._baud_combo.currentText())
        is_sim = "仿真" in port or port == "SIMULATOR"

        self._worker = SerialWorker()
        self._worker.configure(port, slave, baud=baud, timeout=0.1, simulation=is_sim)
        self._worker.reply_ready.connect(self._on_reply)
        self._worker.command_reply.connect(self._on_command_reply)
        self._worker.maintenance_reply.connect(self._on_maintenance_reply)
        self._worker.thresholds_read_reply.connect(self._on_thresholds_read_reply)
        self._worker.thresholds_write_reply.connect(self._on_thresholds_write_reply)
        self._worker.port_error.connect(self._on_port_error)
        self._worker.connected.connect(self._on_connected)
        self._worker.disconnected.connect(self._on_disconnected)
        self._worker.start()

        self._connect_btn.setEnabled(False)
        self._connect_btn.setText("连接中…")
        mode_str = "【虚拟仿真】" if is_sim else f"【真实串口 {port}】"
        self._log(f"正在连接 {mode_str} @ {baud}，从站地址 {slave}")

    def _disconnect(self):
        if self._worker:
            self._worker.stop()
            self._worker = None
        self._connected = False
        self._update_connection_ui()

    def _on_connected(self):
        self._connected = True
        self._connect_btn.setText("断开")
        self._connect_btn.setEnabled(True)
        self._conn_led.set_on(True)
        self._conn_led.set_text("已连接")
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(True)
        self._err_history.clear()
        self._log("通信链路建立成功，开始周期轮询。")
        # 连接后按需读一次保护阈值
        if self._worker:
            self._worker.queue_read_thresholds()

    def _on_disconnected(self):
        self._connected = False
        self._connect_btn.setText("连接")
        self._connect_btn.setEnabled(True)
        self._conn_led.set_on(False)
        self._conn_led.set_text("未连接")
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(False)
        self._btn_clear_fault.setEnabled(False)
        self._btn_reset_cpld.setEnabled(False)
        self._btn_reset_dsp.setEnabled(False)
        self._btn_adc_recal.setEnabled(False)
        self._btn_write_prot.setEnabled(False)
        self._state_value.setText("已离线")
        self._state_value.setStyleSheet("color:#64748b;font-size:22px;font-weight:700;")
        self._maint_ver_hint.setText("固件维护支持：未连接")
        self._maint_ver_hint.setStyleSheet("color:#64748b;font-size:12px;")
        self._log("通信链路已断开。")

    def _on_port_error(self, msg: str):
        self._connected = False
        self._connect_btn.setText("连接")
        self._connect_btn.setEnabled(True)
        self._conn_led.set_on(False, "#e5484d")
        self._conn_led.set_text("连接失败")
        self._btn_clear_fault.setEnabled(False)
        self._btn_reset_cpld.setEnabled(False)
        self._btn_reset_dsp.setEnabled(False)
        self._btn_adc_recal.setEnabled(False)
        self._btn_write_prot.setEnabled(False)
        self._log(f"串口通信错误：{msg}")

    # ------------------------------------------------------------------
    # 周期轮询与数据处理
    # ------------------------------------------------------------------
    def _on_poll_tick(self):
        if not self._connected or not self._worker:
            return
        self._worker.request_input_block()

    def _on_reply(self, result: FrameResult):
        self._tx_count += 1
        if result.request:
            self._tx_hex_edit.setText(" ".join(f"{b:02X}" for b in result.request))
        if result.response:
            self._rx_hex_edit.setText(" ".join(f"{b:02X}" for b in result.response))
            self._rx_count += 1
            if result.elapsed_ms > 0:
                self._latency_sum += result.elapsed_ms
                self._latency_samples += 1

        if result.error:
            if "CRC" in result.error:
                self._crc_err_count += 1
            elif "超时" in result.error:
                self._timeout_count += 1
            self._update_diag_stats()
            return

        if not result.extra:
            return

        values = result.extra
        self._latest_raw = list(values)
        try:
            data = rm.parse_input_registers(list(values), self._vdc_gain, self._vdc_zero)
            data["dsp_online"] = True
            self._latest_data = data
            self._latest_protocol_version = data.get("protocol_version", 0x0000)

            # 版本自适应协商
            if self._worker and self._latest_protocol_version:
                if rm.supports_v103_extended_registers(self._latest_protocol_version):
                    if self._worker.input_quantity != rm.INPUT_QUANTITY_V103:
                        self._worker.set_input_quantity(rm.INPUT_QUANTITY_V103)
                else:
                    if self._worker.input_quantity != rm.INPUT_QUANTITY_V101:
                        self._worker.set_input_quantity(rm.INPUT_QUANTITY_V101)

        except ValueError as exc:
            self._log(f"数据解析异常：{exc}")
            return

        # 检查清故障后验证结果
        if self._pending_fault_clear_verify:
            self._pending_fault_clear_verify = False
            if not data.get("fault_active", False):
                self._log("✅ [清故障校验] DSP 保护与 CPLD 故障已成功清除！当前无活动故障。")
                self._maint_result_label.setText("最近结果：✅ 故障清除成功")
                self._maint_result_label.setStyleSheet("color:#34d399;font-size:12px;")
            else:
                self._log("⚠️ [清故障校验] 锁存故障已执行清除，但硬件故障源仍存在，故障位保持置位。")
                self._maint_result_label.setText("最近结果：⚠️ 硬件故障源仍存在")
                self._maint_result_label.setStyleSheet("color:#f5b83d;font-size:12px;")
            self._maint_status_label.setText("维护状态：空闲")

        self._apply_data(data)
        self._update_registers_table(values, data)
        self._update_diag_stats(data)

        # CSV 实时落盘
        if self._logger.is_recording:
            self._logger.log_record(data, self._latest_raw)
            self._record_status.setText(f"已录制 {self._logger.record_count} 条")

    def _on_command_reply(self, result: FrameResult):
        self._tx_count += 1
        if result.error:
            self._log(f"❌ 命令执行失败：{result.error}")
            return
        if result.parsed and result.parsed.exception_code == 0x03:
            self._exception_count += 1
            self._log("❌ START 启动被从站拒绝（异常 0x03）！请检查电网正半波幅值有效性、PLL 锁定状态，以及是否存在 DSP/CPLD 保护锁存。")
            QMessageBox.warning(
                self,
                "启动联锁拦截",
                "START 启动被 DSP 从站拒绝（Modbus 异常 0x03）！\n\n"
                "可能原因：\n"
                "1. 电网正半波信号未接入或幅值无效；\n"
                "2. SOGI-PLL 锁相环未成功锁定；\n"
                "3. 当前存在未消除的 DSP 软件保护或 CPLD 硬件故障；\n"
                "4. 正在执行校零或复位维护操作。"
            )
            return

        if result.extra is not None:
            cmd_val = result.extra
            name = "启动 (START)" if cmd_val == 1 else "停止 (STOP)"
            self._dsp_request_cmd = cmd_val
            self._log(f"✅ 命令写回成功：DSP 请求值已更新为 {name}")
            self._update_cmd_status_box()

    def _on_maintenance_reply(self, result: FrameResult):
        self._tx_count += 1
        if result.request:
            self._tx_hex_edit.setText(" ".join(f"{b:02X}" for b in result.request))
        if result.response:
            self._rx_hex_edit.setText(" ".join(f"{b:02X}" for b in result.response))
            self._rx_count += 1

        action = self._maintenance_action_name or "维护操作"
        self._maintenance_in_progress = False

        if result.error:
            self._log(f"❌ [{action}] 通信异常：{result.error}")
            self._maint_status_label.setText("维护状态：失败")
            self._maint_result_label.setText(f"最近结果：❌ {result.error}")
            self._maint_result_label.setStyleSheet("color:#e5484d;font-size:12px;")
            return

        if result.parsed and result.parsed.exception_code is not None:
            self._exception_count += 1
            exc_msg = result.parsed.error or f"异常码 0x{result.parsed.exception_code:02X}"
            self._log(f"❌ [{action}] Modbus 异常响应：{exc_msg}")
            self._maint_status_label.setText("维护状态：从站拒绝")
            self._maint_result_label.setText(f"最近结果：❌ {exc_msg}")
            self._maint_result_label.setStyleSheet("color:#e5484d;font-size:12px;")
            return

        if result.extra is True:
            self._log(f"✅ [{action}] DSP 响应回显完全匹配，命令已成功接收。")
            if result.req_type == SerialWorker.REQ_CLEAR_CPLD_FAULT:
                self._pending_fault_clear_verify = True
                self._maint_status_label.setText("维护状态：等待验证故障位")
                self._maint_result_label.setText("最近结果：指令已送达，校验中...")
                self._maint_result_label.setStyleSheet("color:#f5b83d;font-size:12px;")
            elif result.req_type == SerialWorker.REQ_RESET_CPLD:
                self._maint_status_label.setText("维护状态：CPLD 复位已受理")
                self._maint_result_label.setText("最近结果：✅ DSP已接收，正在转发并等待CPLD重连")
                self._maint_result_label.setStyleSheet("color:#f5b83d;font-size:12px;")
                if self._worker:
                    self._worker.queue_read_thresholds()
            elif result.req_type == SerialWorker.REQ_RESET_DSP:
                self._maint_status_label.setText("维护状态：DSP 重启中")
                self._maint_result_label.setText("最近结果：✅ DSP 复位指令已送达，参数恢复默认")
                self._maint_result_label.setStyleSheet("color:#f5b83d;font-size:12px;")
                if self._worker:
                    self._worker.queue_read_thresholds()
            elif result.req_type == SerialWorker.REQ_ADC_RECALIBRATE:
                self._pending_adc_cal_verify = True
                self._maint_status_label.setText("维护状态：ADC 零漂校准中")
                self._maint_result_label.setText("最近结果：指令已接收，等待状态变为 DONE")
                self._maint_result_label.setStyleSheet("color:#f5b83d;font-size:12px;")
        else:
            self._log(f"❌ [{action}] 响应数据不匹配！")
            self._maint_status_label.setText("维护状态：校验失败")
            self._maint_result_label.setText("最近结果：❌ 响应不匹配")
            self._maint_result_label.setStyleSheet("color:#e5484d;font-size:12px;")

    # ------------------------------------------------------------------
    # 数据刷新与界面渲染
    # ------------------------------------------------------------------
    def _apply_data(self, data: dict):
        # 系统状态
        state_text = data["statcom_state"]
        color = state_color(state_text)
        self._state_value.setText(state_text)
        self._state_value.setStyleSheet(f"color:{color};font-size:22px;font-weight:700;")
        self._adc_zero_label.setText(f"ADC 校零状态：{data['adc_zero_state']}")
        if self._pending_adc_cal_verify and data.get("adc_zero_state") == "DONE":
            self._pending_adc_cal_verify = False
            self._maint_status_label.setText("维护状态：ADC 零漂校准完成")
            self._maint_result_label.setText("最近结果：✅ 1024点零漂偏置已更新")
            self._maint_result_label.setStyleSheet("color:#34d399;font-size:12px;")
        self._update_cmd_status_box()

        # CPLD 状态指示灯
        for key, led in self._status_leds.items():
            val = data["cpld_status"].get(key, False)
            if key == "fault_any":
                led.set_on(val, "#e5484d" if val else None)
            else:
                led.set_on(val)

        # 锁相环状态
        self._pll_locked_led.set_on(data["pll_locked"])
        self._pll_valid_led.set_on(data["pll_signal_valid"])

        # 测量卡片更新
        self._card_grid_v.set_value(f"{data['grid_voltage']:.1f}")
        self._card_grid_v.set_secondary(f"原始码: {data['grid_halfwave_raw']}")

        self._card_iac.set_value(f"{data['iac']:+.2f}")
        self._card_iac.set_secondary(f"瞬时: {data['iac_raw']} | 零点: {data.get('iac_zero_raw', '--')}")

        self._card_vdc.set_value(f"{data['cpld_vdc_v']:.1f}")
        self._card_vdc.set_secondary(f"平均: {data['cpld_vdc_average']} | 瞬时: {data['cpld_vdc_raw']} | Zero={self._vdc_zero:.1f}")

        self._card_pll_f.set_value(f"{data['pll_frequency']:.2f}")
        self._card_pll_f.set_secondary("✅ 锁相环已锁定" if data["pll_locked"] else "❌ 未锁定")

        self._card_temp.set_value(f"{data['temperature_frequency_hz']:.0f}")
        self._card_temp.set_secondary(f"计数: {data['temperature_count']} count/100ms | 门限: 5000")

        self._card_adc_count.set_value(f"{data['adc_sample_count']}")
        self._card_adc_count.set_secondary(f"协议版本: 0x{data['protocol_version']:04X}")

        # 底部运行时间
        self._uptime_label.setText(f"DSP 运行时间：{format_uptime(data['uptime_ms'])}")

        # 故障与保护独立表更新
        self._update_fault_tables(data)

        # 维护与写入按钮使能状态联锁刷新
        self._update_maintenance_ui_state(data)

        # 波形追加
        if not self._plot_paused:
            now = time.monotonic()
            self._t_buffer.append(now)
            self._iac_buffer.append(data["iac"])
            self._vdc_buffer.append(data["cpld_vdc_v"])
            self._vgrid_buffer.append(data["grid_voltage"])
            self._pll_buffer.append(data["pll_frequency"])

            xs = list(range(len(self._t_buffer)))
            if self._chk_iac.isChecked():
                self._curve_iac.setData(xs, list(self._iac_buffer))
            else:
                self._curve_iac.clear()

            if self._chk_vdc.isChecked():
                self._curve_vdc.setData(xs, list(self._vdc_buffer))
            else:
                self._curve_vdc.clear()

            if self._chk_vgrid.isChecked():
                self._curve_vgrid.setData(xs, list(self._vgrid_buffer))
            else:
                self._curve_vgrid.clear()

            if self._chk_pll.isChecked():
                self._curve_pll.setData(xs, list(self._pll_buffer))
            else:
                self._curve_pll.clear()

    def _update_registers_table(self, values: list[int] | tuple[int, ...], data: dict):
        """刷新 29 寄存器独立全表视图中的实时值。"""
        v_list = list(values)
        proto_ver = data.get("protocol_version", 0x0000)
        self._reg_proto_label.setText(f"当前固件协议：0x{proto_ver:04X} ({len(v_list)} 寄存器)")

        for row in range(29):
            if row < len(v_list):
                val = v_list[row]
                self._full_reg_table.item(row, 4).setText(f"0x{val:04X}")
                self._full_reg_table.item(row, 5).setText(str(val))

                interp = rm.format_register_interpreted_value(
                    row, val, v_list, self._vdc_gain, self._vdc_zero, rm.DEFAULT_K_GRID, rm.DEFAULT_K_IAC
                )
                self._full_reg_table.item(row, 6).setText(interp)
            else:
                self._full_reg_table.item(row, 4).setText("--")
                self._full_reg_table.item(row, 5).setText("--")
                self._full_reg_table.item(row, 6).setText("固件不支持 (需 0x0103+)")

    def _update_maintenance_ui_state(self, data: dict):
        proto_ver = data.get("protocol_version", 0x0000)
        supported = rm.supports_remote_maintenance(proto_ver)
        reset_supported = rm.supports_remote_reset(proto_ver)
        adc_cal_supported = rm.supports_adc_recalibration(proto_ver)
        prot_supported = rm.supports_protection_thresholds(proto_ver)
        link_online = data.get("cpld_link", {}).get("online", False)
        echo_val = data.get("cpld_command_echo_raw", 0)
        is_stop = (self._dsp_request_cmd == 0) and (echo_val == 0)

        if not supported:
            self._btn_clear_fault.setEnabled(False)
            self._btn_reset_cpld.setEnabled(False)
            self._btn_reset_dsp.setEnabled(False)
            self._btn_adc_recal.setEnabled(False)
            self._btn_write_prot.setEnabled(False)
            self._maint_ver_hint.setText(f"固件版本 0x{proto_ver:04X} 不支持远程维护 (需 0x0101 及以上)")
            self._maint_ver_hint.setStyleSheet("color:#f5b83d;font-size:12px;")
            return

        self._maint_ver_hint.setText(f"固件版本 0x{proto_ver:04X} 支持远程维护与保护阈值")
        self._maint_ver_hint.setStyleSheet("color:#34d399;font-size:12px;")

        if self._maintenance_in_progress:
            self._btn_clear_fault.setEnabled(False)
            self._btn_reset_cpld.setEnabled(False)
            self._btn_reset_dsp.setEnabled(False)
            self._btn_adc_recal.setEnabled(False)
            self._btn_write_prot.setEnabled(False)
            self._start_btn.setEnabled(False)
        else:
            self._btn_clear_fault.setEnabled(self._connected and is_stop)
            self._btn_reset_cpld.setEnabled(self._connected and reset_supported and link_online and is_stop)
            self._btn_reset_dsp.setEnabled(self._connected and reset_supported and is_stop)
            self._btn_adc_recal.setEnabled(
                self._connected and adc_cal_supported and is_stop and
                data.get("adc_zero_state") != "RUNNING"
            )
            self._btn_write_prot.setEnabled(self._connected and prot_supported and is_stop)
            if self._connected:
                self._start_btn.setEnabled(True)

    def _update_cmd_status_box(self):
        if not self._latest_data:
            return
        echo_val = self._latest_data.get("cpld_command_echo_raw", 0)
        dsp_req = "START" if self._dsp_request_cmd == 1 else "STOP"
        echo_str = "START" if echo_val == 1 else "STOP"

        if self._dsp_request_cmd == echo_val:
            status_text = f"请求: {dsp_req} | CPLD回显: {echo_str} (一致)"
            color = "#34d399" if echo_val == 1 else "#8fa1b3"
        else:
            status_text = f"请求: {dsp_req} | CPLD回显: {echo_str} (等待同步)"
            color = "#f5b83d"
        self._cmd_status_box.setText(status_text)
        self._cmd_status_box.setStyleSheet(f"color:{color};font-size:12px;font-weight:600;")

    def _update_fault_tables(self, data: dict):
        # 1. DSP 保护锁存独立表 (0x000C)
        dsp_map = [
            (0, "grid_peak_overvoltage", "电网峰值软件过压"),
            (1, "iac_instant_overcurrent", "交流电流瞬时软件过流"),
            (2, "pll_loss", "PLL曾锁定后失锁"),
            (3, "cpld_link_fault", "DSP-CPLD通信离线"),
            (4, "cpld_reported_fault", "CPLD上报故障"),
        ]
        dsp_active = []
        for row, (bit, key, desc) in enumerate(dsp_map):
            is_active = data.get("dsp_protection", {}).get(key, False)
            if is_active:
                dsp_active.append(desc)

            bit_item = QTableWidgetItem(f"Bit {bit:02d}")
            bit_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            desc_item = QTableWidgetItem(desc)

            if is_active:
                status_item = QTableWidgetItem("⚠️ 保护触发")
                status_item.setForeground(QColor("#e5484d"))
                desc_item.setForeground(QColor("#e5484d"))
            else:
                status_item = QTableWidgetItem("正常")
                status_item.setForeground(QColor("#34d399"))
                desc_item.setForeground(QColor("#8fa1b3"))

            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._dsp_prot_table.setRowHeight(row, 26)
            self._dsp_prot_table.setItem(row, 0, bit_item)
            self._dsp_prot_table.setItem(row, 1, desc_item)
            self._dsp_prot_table.setItem(row, 2, status_item)

        if dsp_active:
            self._dsp_prot_summary_label.setText(f"⚠️ DSP 保护触发 ({len(dsp_active)}项): " + "、".join(dsp_active))
            self._dsp_prot_summary_label.setStyleSheet("color:#e5484d;font-size:12px;font-weight:600;")
        else:
            self._dsp_prot_summary_label.setText("✅ DSP 保护正常，无锁存报警")
            self._dsp_prot_summary_label.setStyleSheet("color:#34d399;font-size:12px;font-weight:600;")

        # 2. CPLD 硬件故障独立表 (0x000D)
        cpld_map = [
            (0, "bypass_status", "旁路状态异常"),
            (1, "peer_module_fault", "对端模块故障"),
            (4, "dc_overvoltage_fault", "直流硬件过压故障"),
            (5, "drive_fault_1", "驱动 1 硬件故障"),
            (6, "drive_fault_2", "驱动 2 硬件故障"),
            (7, "software_vdc_overvoltage", "软件直流过压告警"),
            (8, "temperature_over", "温度过高频率异常"),
            (10, "cpld_rx_error_latch", "CPLD通信接收错误锁存"),
            (11, "temperature_sensor_fault", "温度传感器无脉冲故障"),
            (12, "precharge_under", "预充电欠压"),
            (13, "precharge_over", "预充电过压"),
            (14, "precharge_timeout", "预充电超时"),
            (15, "adc_stale", "CPLD ADC 采样超时"),
        ]

        link_online = data.get("cpld_link", {}).get("online", False)
        cpld_active = []

        for row, (bit, key, desc) in enumerate(cpld_map):
            is_active = data.get("cpld_fault", {}).get(key, False)
            if is_active:
                cpld_active.append(desc)

            bit_item = QTableWidgetItem(f"Bit {bit:02d}")
            bit_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            desc_item = QTableWidgetItem(desc)

            if not link_online:
                status_item = QTableWidgetItem("❓ 未知 (CPLD离线)")
                status_item.setForeground(QColor("#8fa1b3"))
                desc_item.setForeground(QColor("#8fa1b3"))
            elif is_active:
                status_item = QTableWidgetItem("⚠️ 告警触发")
                status_item.setForeground(QColor("#e5484d"))
                desc_item.setForeground(QColor("#e5484d"))
            else:
                status_item = QTableWidgetItem("正常")
                status_item.setForeground(QColor("#34d399"))
                desc_item.setForeground(QColor("#8fa1b3"))

            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._fault_table.setRowHeight(row, 26)
            self._fault_table.setItem(row, 0, bit_item)
            self._fault_table.setItem(row, 1, desc_item)
            self._fault_table.setItem(row, 2, status_item)

        if not link_online:
            self._fault_summary_label.setText("⚠️ DSP 与 CPLD 通信离线 (SCI-A 超时)，无法读取 CPLD 实际硬件故障！")
            self._fault_summary_label.setStyleSheet("color:#f5b83d;font-size:12px;font-weight:600;")
        elif cpld_active:
            summary = f"⚠️ CPLD 硬件故障 ({len(cpld_active)}项): " + "、".join(cpld_active)
            self._fault_summary_label.setText(summary)
            self._fault_summary_label.setStyleSheet("color:#e5484d;font-size:12px;font-weight:600;")
        else:
            self._fault_summary_label.setText("✅ CPLD 硬件无锁存故障")
            self._fault_summary_label.setStyleSheet("color:#34d399;font-size:12px;font-weight:600;")

    def _update_diag_stats(self, data: dict | None = None):
        # A. PC 本地串口统计
        self._diag_tx.set_value(str(self._tx_count))
        self._diag_rx.set_value(str(self._rx_count))
        self._diag_crc.set_value(str(self._crc_err_count))
        self._diag_timeout.set_value(str(self._timeout_count))
        self._diag_exception.set_value(str(self._exception_count))
        avg_lat = (self._latency_sum / self._latency_samples) if self._latency_samples > 0 else 0.0
        self._diag_latency.set_value(f"{avg_lat:.1f}")

        if not data:
            return

        # 维护 5 秒历史滑动窗口
        now = time.monotonic()
        curr_counts = {
            "pc_dsp": data.get("pc_dsp_error_count", 0),
            "dsp_cpld_total": data.get("dsp_cpld_error_count", 0),
            "cpld_uart": data.get("cpld_uart_error_count", 0),
            "cpld_crc": data.get("cpld_crc_error_count", 0),
            "cpld_incomplete": data.get("cpld_incomplete_frame_count", 0),
            "dsp_format": data.get("dsp_scia_format_error_count", 0),
            "dsp_overflow": data.get("dsp_scia_overflow_count", 0),
            "dsp_timeout": data.get("dsp_cpld_timeout_count", 0),
        }
        self._err_history.append((now, curr_counts))
        while self._err_history and (now - self._err_history[0][0] > 5.0):
            self._err_history.popleft()

        oldest_counts = self._err_history[0][1] if self._err_history else curr_counts

        def format_metric(key: str, card: StatCard):
            curr_val = curr_counts[key]
            old_val = oldest_counts[key]
            delta = rm.calc_delta_u16(curr_val, old_val)
            card.set_value(str(curr_val))
            if delta == 0:
                card.set_secondary("🟢 5s增量: 0 (正常)")
            else:
                card.set_secondary(f"🟡 5s增量: +{delta} (异常)")

        # B. DSP 端 PC-SCI-B 统计
        format_metric("pc_dsp", self._diag_pc_dsp)

        # C. DSP 接收 CPLD 统计
        format_metric("dsp_cpld_total", self._diag_dsp_cpld_total)
        format_metric("dsp_format", self._diag_dsp_format)
        format_metric("dsp_overflow", self._diag_dsp_overflow)
        format_metric("dsp_timeout", self._diag_dsp_timeout)

        # D. CPLD 接收 DSP 统计
        format_metric("cpld_uart", self._diag_cpld_uart)
        format_metric("cpld_crc", self._diag_cpld_crc)
        format_metric("cpld_incomplete", self._diag_cpld_incomplete)

        bit10_active = data.get("cpld_fault", {}).get("cpld_rx_error_latch", False)
        if bit10_active:
            self._diag_bit10_latch.set_value("⚠️ 已锁存")
            self._diag_bit10_latch.set_secondary("历史出现过通信错误")
        else:
            self._diag_bit10_latch.set_value("正常")
            self._diag_bit10_latch.set_secondary("未置位锁存")

    # ------------------------------------------------------------------
    # 控制交互与安全弹窗
    # ------------------------------------------------------------------
    def _on_start_clicked(self):
        if not self._connected or not self._worker:
            self._log("未连接，无法发送启动命令。")
            return

        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("⚠️ 启动命令二次确认")
        msg_box.setIcon(QMessageBox.Icon.Warning)
        msg_box.setText(
            "<h3>确认发送 START 调试命令？</h3>"
            "<p><b>安全声明：</b>当前固件处于调试阶段，功率桥臂与继电器处于<b>硬件封锁</b>状态。</p>"
            "<p>发送 START 将触发 DSP/CPLD 调试状态机演进（进入 RUN 状态回显）。</p>"
        )
        msg_box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        msg_box.setDefaultButton(QMessageBox.StandardButton.Cancel)

        if msg_box.exec() == QMessageBox.StandardButton.Yes:
            self._worker.queue_start()
            self._log(">>> [用户操作] 确认发送 START (0x0100=1)")

    def _on_stop_clicked(self):
        if not self._connected or not self._worker:
            self._log("未连接，无法发送停止命令。")
            return
        # STOP 按钮一键直达，最高优先级
        self._worker.queue_stop()
        self._dsp_request_cmd = 0
        self._log(">>> [用户操作] 立即发送最高优先级 STOP (0x0100=0)")

    def _on_clear_fault_clicked(self):
        if not self._connected or not self._worker:
            return

        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("⚠️ 清除锁存故障确认")
        msg_box.setIcon(QMessageBox.Icon.Question)
        msg_box.setText(
            "<h3>确认清除 DSP 保护与 CPLD 锁存故障？</h3>"
            "<p><b>安全说明：</b>该操作只清除<b>已经消除</b>的锁存故障。</p>"
            "<p>仍然存在的硬件故障将继续保持置位，不会解除 PWM 安全封锁。</p>"
        )
        msg_box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        msg_box.setDefaultButton(QMessageBox.StandardButton.Cancel)

        if msg_box.exec() == QMessageBox.StandardButton.Yes:
            self._maintenance_in_progress = True
            self._maintenance_action_name = "清除故障"
            self._maint_status_label.setText("维护状态：发送清障指令中...")
            self._log(">>> [用户操作] 请求清除锁存故障 (0x0101=0xA55A)")
            self._worker.queue_clear_cpld_fault()

    def _on_reset_cpld_clicked(self):
        if not self._connected or not self._worker:
            return

        dlg = MaintenanceConfirmDialog(
            self,
            title="⚠️ 复位 CPLD 通信与状态机确认",
            prompt="复位操作将重置 CPLD UART 通信、Modbus 状态机及 ADC 采样逻辑。",
            required_keyword="RESET CPLD",
            warning_text="请确认系统已处于 STOP 停机态！操作期间将短暂中断通信。",
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._maintenance_in_progress = True
            self._maintenance_action_name = "复位 CPLD"
            self._maint_status_label.setText("维护状态：复位指令下发中...")
            self._log(">>> [用户操作] 执行复位 CPLD (0x0102=0xC33C)")
            self._worker.queue_reset_cpld()

    def _on_reset_dsp_clicked(self):
        if not self._connected or not self._worker:
            return

        dlg = MaintenanceConfirmDialog(
            self,
            title="⚠️ 受控软复位 DSP 控制器确认",
            prompt="软复位将重新初始化 DSP 内部状态机并使运行时间、采样计数及保护阈值恢复默认。",
            required_keyword="RESET DSP",
            warning_text="请确认系统已停机！DSP 将执行重启并重置所有运行状态为 STOP。",
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._maintenance_in_progress = True
            self._maintenance_action_name = "复位 DSP"
            self._maint_status_label.setText("维护状态：复位指令下发中...")
            self._log(">>> [用户操作] 执行复位 DSP (0x0103=0xD55D)")
            self._worker.queue_reset_dsp()

    def _on_adc_recalibrate_clicked(self):
        if not self._connected or not self._worker:
            return

        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("⚠️ ADC 零漂重新校准确认")
        msg_box.setIcon(QMessageBox.Icon.Warning)
        msg_box.setText(
            "<h3>确认重新执行 ADC 电流零漂校准？</h3>"
            "<p>请保持系统处于 STOP，交流与直流电流传感器输入均为真实零电流。</p>"
            "<p>DSP将在约51.2ms内采集1024点并更新零漂偏置。</p>"
        )
        msg_box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        msg_box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if msg_box.exec() == QMessageBox.StandardButton.Yes:
            self._maintenance_in_progress = True
            self._maintenance_action_name = "ADC 零漂重新校准"
            self._maint_status_label.setText("维护状态：校准指令下发中...")
            self._log(">>> [用户操作] ADC重新零漂校准 (0x0104=0xCA1B)")
            self._worker.queue_adc_recalibrate()

    def _toggle_plot_pause(self):
        self._plot_paused = not self._plot_paused
        self._btn_pause.setText("▶ 继续刷新" if self._plot_paused else "⏸ 暂停刷新")

    def _clear_plot(self):
        self._t_buffer.clear()
        self._iac_buffer.clear()
        self._vdc_buffer.clear()
        self._vgrid_buffer.clear()
        self._pll_buffer.clear()
        self._curve_iac.clear()
        self._curve_vdc.clear()
        self._curve_vgrid.clear()
        self._curve_pll.clear()
        self._log("已清空趋势曲线缓存。")

    def _toggle_recording(self):
        if self._logger.is_recording:
            saved_path = self._logger.stop_recording()
            self._record_btn.setText("🔴 开始 CSV 记录")
            self._record_btn.setStyleSheet("")
            self._record_status.setText(f"已保存: {os.path.basename(saved_path)}")
            self._log(f"CSV 数据记录已停止，文件保存至：{saved_path}")
        else:
            path = self._logger.start_recording()
            self._record_btn.setText("⏹ 停止 CSV 记录")
            self._record_btn.setStyleSheet("background-color:#e5484d;color:#ffffff;")
            self._record_status.setText("正在记录...")
            self._log(f"开始 CSV 数据记录，目标文件：{path}")

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------
    def _update_connection_ui(self):
        if self._connected:
            self._connect_btn.setText("断开")
            self._conn_led.set_on(True)
            self._conn_led.set_text("已连接")
        else:
            self._connect_btn.setText("连接")
            self._conn_led.set_on(False)
            self._conn_led.set_text("未连接")

    def _update_clock(self):
        self._clock_label.setText(time.strftime("%Y-%m-%d %H:%M:%S"))

    def _log(self, msg: str):
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        self._dash_log.appendPlainText(line)
        self._event_log_view.appendPlainText(line)

    def closeEvent(self, event: QCloseEvent):
        if self._logger.is_recording:
            self._logger.stop_recording()
        if self._worker:
            self._worker.stop()
            self._worker = None
        super().closeEvent(event)
