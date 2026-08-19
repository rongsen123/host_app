"""后台串口通信工作线程与 Modbus RTU 事务处理器。

包含：
- 虚拟 DSP 从站仿真器 DspSimulator（支持 0x0105 协议 29 寄存器、0007 零电流码、保护阈值原始码读写、0x0101 清故障、0x0102 复位、0x0104 ADC校零）
- 基于 PriorityQueue 的优先级串口事务调度工作线程 SerialWorker（带精准 Modbus 帧长预测与抗分包粘包接收引擎）
"""

from __future__ import annotations

import heapq
import itertools
import math
import queue
import random
import time
from typing import Callable

import serial
from PySide6.QtCore import QThread, Signal

from communication.crc16 import append_crc, verify_crc
from communication.modbus_rtu import (
    ModbusResponse,
    build_read_request,
    build_write_single_request,
    parse_holding_read_response,
    parse_read_response,
    parse_response,
    parse_write_single_response,
    parse_write_single_response_exact,
)
from protocol.register_model import (
    ADDR_ADC_RECALIBRATE,
    ADDR_CLEAR_CPLD_FAULT,
    ADDR_COMMAND,
    ADDR_PROT_GRID_PEAK_OV,
    ADDR_PROT_IAC_OC,
    ADDR_PROT_VDC_OV,
    ADDR_RESET_CPLD,
    ADDR_RESET_DSP,
    CMD_START,
    CMD_STOP,
    INPUT_QUANTITY_V101,
    INPUT_QUANTITY_V103,
    KEY_ADC_RECALIBRATE,
    KEY_CLEAR_CPLD_FAULT,
    KEY_RESET_CPLD,
    KEY_RESET_DSP,
    PROT_GRID_PEAK_OV_DEFAULT_RAW_V105,
    PROT_GRID_PEAK_OV_MAX_RAW_V105,
    PROT_IAC_OC_DEFAULT_RAW_V105,
    PROT_IAC_OC_MAX_RAW_V105,
    PROT_VDC_OV_DEFAULT_RAW_V105,
    PROT_VDC_OV_MAX_RAW_V105,
    PROTOCOL_VERSION_V105,
)

INTER_FRAME_GAP = 0.00175  # 固定 t3.5 = 1.75 ms


class FrameResult:
    """一次收发结果的轻量载体。"""

    def __init__(
        self,
        request: bytes,
        response: bytes | None,
        parsed: ModbusResponse | None,
        extra: object | None,
        elapsed_ms: float,
        error: str | None,
        req_type: str | None = None,
    ):
        self.request = request
        self.response = response
        self.parsed = parsed
        self.extra = extra
        self.elapsed_ms = elapsed_ms
        self.error = error
        self.req_type = req_type


class DspSimulator:
    """虚拟 DSP + CPLD Modbus 从站仿真器。

    支持 0x0105 协议 29 寄存器、0x1000~0x1002 保护阈值原始码读写、STOP/START、
    CPLD 锁存故障清除、CPLD/DSP 复位和 ADC 重新校零。
    """

    def __init__(self, slave: int = 2, protocol_version: int = PROTOCOL_VERSION_V105):
        self.slave = slave
        self.protocol_version = protocol_version
        self.start_time = time.time()
        self.adc_sample_count = 0
        self.command_holding = 0  # 0=STOP, 1=START
        self.command_echo = 0     # 0=STOP, 1=START
        self.pll_locked = True
        self.pll_frequency = 5000 # 50.00 Hz
        self.pll_signal_valid = True
        self.statcom_state = 1    # ADC_VERIFY
        self.adc_zero_state = 2   # DONE
        self.adc_calibration_samples = 1024
        self.dsp_cpld_errors = 0
        self.pc_dsp_errors = 0
        self.cpld_status = 0x0017 # link_online, adc_valid, temp_valid, pwm_healthy
        self.cpld_fault = 0x00000000
        self.active_fault = 0x00000000  # 硬件持续故障源（不可清除）
        self.cpld_link = 0x0001   # online

        # V0105 保护状态与原始码阈值初值 (count)
        self.dsp_protection_latched = 0
        self.cpld_fault_low = 0
        self.vdc_overvoltage_dv = PROT_VDC_OV_DEFAULT_RAW_V105       # 3410 count (约 974.2 V)
        self.grid_peak_overvoltage_dv = PROT_GRID_PEAK_OV_DEFAULT_RAW_V105 # 1313 count (约 360.0 V)
        self.iac_overcurrent_ca = PROT_IAC_OC_DEFAULT_RAW_V105       # 51 count (约 5.04 A)

        # 扩展诊断错误计数
        self.cpld_uart_errors = 0
        self.cpld_crc_errors = 0
        self.cpld_incomplete_frame_errors = 0
        self.dsp_scia_format_errors = 0
        self.dsp_scia_overflow_errors = 0
        self.dsp_cpld_timeouts = 0

    def set_fault(self, latch_fault: int = 0, active_fault: int = 0):
        self.active_fault = active_fault
        self.cpld_fault = latch_fault | active_fault
        self.cpld_fault_low = self.cpld_fault & 0xFFFF
        if self.cpld_fault != 0:
            self.cpld_status |= 0x0008
        else:
            self.cpld_status &= ~0x0008

    def handle_request(self, frame: bytes) -> bytes | None:
        if len(frame) < 4:
            return None
        if not verify_crc(frame):
            return None

        slave = frame[0]
        if slave != self.slave:
            return None

        func = frame[1]
        now = time.time()
        uptime_ms = int((now - self.start_time) * 1000) & 0xFFFFFFFF
        self.adc_sample_count = (self.adc_sample_count + 128) & 0xFFFFFFFF

        # ADC 校零状态演进
        if self.adc_zero_state == 1:
            self.adc_calibration_samples += 128
            if self.adc_calibration_samples >= 1024:
                self.adc_zero_state = 2  # DONE

        # 状态机演进
        if self.command_holding == 1:
            self.statcom_state = 7  # RUN
        elif self.cpld_fault != 0 or self.dsp_protection_latched != 0:
            self.statcom_state = 8  # FAULT
        else:
            self.statcom_state = 1  # ADC_VERIFY

        # 模拟量生成
        theta = (uptime_ms % 20) / 20.0 * 2.0 * math.pi
        grid_raw = int(1200.0 * max(0.0, math.sin(theta)) + random.uniform(-3, 3))
        # 零电流基准码约 2048
        iac_zero = 2048
        iac_raw = int(iac_zero + 24 * math.sin(theta - math.pi / 2) + random.uniform(-2, 2))

        vdc_avg = int(1276 + (15 if self.command_echo == 1 else 0) + random.uniform(-2, 2))
        vdc_raw = int(vdc_avg + random.uniform(-4, 4))
        temp_count = int(1050 + random.uniform(-5, 5))

        if func == 0x04:  # 读输入寄存器 (FC04)
            start_addr = (frame[2] << 8) | frame[3]
            qty = (frame[4] << 8) | frame[5]
            if start_addr == 0x0000:
                full_regs = [
                    self.protocol_version,              # 0000 (0x0105)
                    (uptime_ms >> 16) & 0xFFFF,         # 0001
                    uptime_ms & 0xFFFF,                 # 0002
                    (self.adc_sample_count >> 16) & 0xFFFF, # 0003
                    self.adc_sample_count & 0xFFFF,     # 0004
                    grid_raw,                           # 0005 (ADCINA0 原始码)
                    iac_raw,                            # 0006 (ADCINA2 瞬时原始码)
                    iac_zero,                           # 0007 (V0105: 零电流校准原始码)
                    vdc_raw,                            # 0008 (CPLD Vdc 瞬时原始码)
                    vdc_avg,                            # 0009 (CPLD Vdc 平均原始码)
                    temp_count,                         # 000A (IGBT 温度脉冲计数/100ms)
                    self.cpld_status,                   # 000B
                    self.dsp_protection_latched,        # 000C (DSP保护锁存)
                    self.cpld_fault_low,                # 000D (CPLD故障低16位)
                    self.cpld_link,                     # 000E
                    1 if self.pll_locked else 0,        # 000F
                    self.pll_frequency + int(random.uniform(-5, 5)), # 0010 (50.00 Hz)
                    1 if self.pll_signal_valid else 0,  # 0011
                    self.statcom_state,                 # 0012
                    self.adc_zero_state,                # 0013
                    self.dsp_cpld_errors,               # 0014
                    self.pc_dsp_errors,                 # 0015
                    self.command_echo,                  # 0016
                    self.cpld_uart_errors,              # 0017
                    self.cpld_crc_errors,               # 0018
                    self.cpld_incomplete_frame_errors,  # 0019
                    self.dsp_scia_format_errors,        # 001A
                    self.dsp_scia_overflow_errors,      # 001B
                    self.dsp_cpld_timeouts,             # 001C
                ]
                if qty <= len(full_regs):
                    regs = full_regs[:qty]
                    byte_count = qty * 2
                    payload = bytearray([slave, func, byte_count])
                    for r in regs:
                        payload.extend([(r >> 8) & 0xFF, r & 0xFF])
                    return append_crc(bytes(payload))
                else:
                    return append_crc(bytes([slave, 0x84, 0x02]))

        elif func == 0x03:  # 读保持寄存器 (FC03)
            start_addr = (frame[2] << 8) | frame[3]
            qty = (frame[4] << 8) | frame[5]

            # 读命令保持寄存器
            if start_addr == ADDR_COMMAND and qty == 1:
                payload = bytearray([slave, func, 2, (self.command_holding >> 8) & 0xFF, self.command_holding & 0xFF])
                return append_crc(bytes(payload))

            # 读保护阈值保持寄存器 (0x1000~0x1002，返回原始码)
            if start_addr == ADDR_PROT_VDC_OV:
                threshold_list = [self.vdc_overvoltage_dv, self.grid_peak_overvoltage_dv, self.iac_overcurrent_ca]
                if qty <= len(threshold_list):
                    regs = threshold_list[:qty]
                    payload = bytearray([slave, func, qty * 2])
                    for r in regs:
                        payload.extend([(r >> 8) & 0xFF, r & 0xFF])
                    return append_crc(bytes(payload))
                return append_crc(bytes([slave, 0x83, 0x02]))

            if 0x1000 <= start_addr <= 0x1002:
                threshold_map = {
                    0x1000: self.vdc_overvoltage_dv,
                    0x1001: self.grid_peak_overvoltage_dv,
                    0x1002: self.iac_overcurrent_ca,
                }
                regs = [threshold_map[addr] for addr in range(start_addr, start_addr + qty) if addr in threshold_map]
                if len(regs) == qty:
                    payload = bytearray([slave, func, qty * 2])
                    for r in regs:
                        payload.extend([(r >> 8) & 0xFF, r & 0xFF])
                    return append_crc(bytes(payload))
                return append_crc(bytes([slave, 0x83, 0x02]))

            return append_crc(bytes([slave, 0x83, 0x02]))

        elif func == 0x06:  # 写单个保持寄存器 (FC06)
            addr = (frame[2] << 8) | frame[3]
            val = (frame[4] << 8) | frame[5]

            if addr == ADDR_COMMAND:
                if val in (CMD_STOP, CMD_START):
                    if val == CMD_START:
                        # 检查启动联锁
                        if not self.pll_signal_valid or not self.pll_locked or self.cpld_fault != 0 or self.dsp_protection_latched != 0:
                            return append_crc(bytes([slave, 0x86, 0x03]))
                    self.command_holding = val
                    self.command_echo = val  # 模拟硬件回显
                    return frame  # FC06 返回原请求帧
                else:
                    return append_crc(bytes([slave, 0x86, 0x03]))

            # 维护寄存器检查版本支持
            if addr in (ADDR_CLEAR_CPLD_FAULT, ADDR_RESET_CPLD,
                        ADDR_RESET_DSP, ADDR_ADC_RECALIBRATE):
                required_version = 0x0101 if addr == ADDR_CLEAR_CPLD_FAULT else 0x0102
                if self.protocol_version < required_version:
                    return append_crc(bytes([slave, 0x86, 0x02]))

                if addr == ADDR_CLEAR_CPLD_FAULT:
                    if val == KEY_CLEAR_CPLD_FAULT and self.command_holding == CMD_STOP:
                        self.cpld_fault = self.active_fault
                        self.cpld_fault_low = self.cpld_fault & 0xFFFF
                        self.dsp_protection_latched = 0
                        if self.cpld_fault == 0:
                            self.cpld_status &= ~0x0008
                        return frame
                    else:
                        return append_crc(bytes([slave, 0x86, 0x03]))

                elif addr == ADDR_ADC_RECALIBRATE:
                    if (val == KEY_ADC_RECALIBRATE and
                            self.command_holding == CMD_STOP and
                            self.adc_zero_state != 1):
                        self.adc_zero_state = 1
                        self.adc_calibration_samples = 0
                        return frame
                    else:
                        return append_crc(bytes([slave, 0x86, 0x03]))

                elif addr == ADDR_RESET_CPLD:
                    if val == KEY_RESET_CPLD and self.command_holding == CMD_STOP:
                        self.command_echo = CMD_STOP
                        self.cpld_fault = self.active_fault
                        self.cpld_fault_low = self.cpld_fault & 0xFFFF
                        self.cpld_status = 0x0017 | (0x0008 if self.cpld_fault else 0)
                        self.cpld_link = 0x0001
                        return frame
                    return append_crc(bytes([slave, 0x86, 0x03]))

                elif addr == ADDR_RESET_DSP:
                    if val == KEY_RESET_DSP and self.command_holding == CMD_STOP:
                        self.start_time = time.time()
                        self.adc_sample_count = 0
                        self.command_holding = CMD_STOP
                        self.command_echo = CMD_STOP
                        self.statcom_state = 1
                        self.adc_zero_state = 1
                        self.adc_calibration_samples = 0
                        self.dsp_protection_latched = 0
                        self.vdc_overvoltage_dv = PROT_VDC_OV_DEFAULT_RAW_V105
                        self.grid_peak_overvoltage_dv = PROT_GRID_PEAK_OV_DEFAULT_RAW_V105
                        self.iac_overcurrent_ca = PROT_IAC_OC_DEFAULT_RAW_V105
                        return frame
                    return append_crc(bytes([slave, 0x86, 0x03]))

            # V0105 保护阈值原始码写入 (0x1000~0x1002)
            if addr in (ADDR_PROT_VDC_OV, ADDR_PROT_GRID_PEAK_OV, ADDR_PROT_IAC_OC):
                if self.command_holding == CMD_START:
                    return append_crc(bytes([slave, 0x86, 0x03]))  # 仅 STOP 可写

                if addr == ADDR_PROT_VDC_OV:
                    if not (1 <= val <= PROT_VDC_OV_MAX_RAW_V105):
                        return append_crc(bytes([slave, 0x86, 0x03]))
                    self.vdc_overvoltage_dv = val
                    return frame
                elif addr == ADDR_PROT_GRID_PEAK_OV:
                    if not (1 <= val <= PROT_GRID_PEAK_OV_MAX_RAW_V105):
                        return append_crc(bytes([slave, 0x86, 0x03]))
                    self.grid_peak_overvoltage_dv = val
                    return frame
                elif addr == ADDR_PROT_IAC_OC:
                    if not (1 <= val <= PROT_IAC_OC_MAX_RAW_V105):
                        return append_crc(bytes([slave, 0x86, 0x03]))
                    self.iac_overcurrent_ca = val
                    return frame

            # 其他未实现地址返回异常 02
            return append_crc(bytes([slave, 0x86, 0x02]))

        return None


class SerialWorker(QThread):
    """串口工作线程，处理普通读取、命令下发与保护阈值配置。

    使用线程安全 PriorityQueue 进行调度：
      - 优先级 0: STOP（最高优先级，入队时丢弃待发 START 与写阈值请求）
      - 优先级 1: 维护命令（清故障 / CPLD复位 / DSP复位 / ADC重新校零）
      - 优先级 2: START
      - 优先级 3: 保护阈值批量写入 (FC06 x 3)
      - 优先级 10: FC03 按需读取 (读运行命令 / 读保护阈值)
      - 优先级 20: 周期 FC04 读取（单例入队，不重复堆积）
    """

    reply_ready = Signal(object)          # FrameResult（普通周期读取结果）
    command_reply = Signal(object)        # FrameResult（STOP/START命令结果）
    maintenance_reply = Signal(object)    # FrameResult（维护命令结果）
    thresholds_read_reply = Signal(object) # FrameResult (读保护阈值结果)
    thresholds_write_reply = Signal(object)# FrameResult (写保护阈值结果)
    port_error = Signal(str)
    connected = Signal()
    disconnected = Signal()

    PRIO_STOP = 0
    PRIO_MAINTENANCE = 1
    PRIO_START = 2
    PRIO_WRITE_THRESHOLDS = 3
    PRIO_READ_HOLDING = 10
    PRIO_READ_INPUT = 20

    REQ_STOP = "req_stop"
    REQ_START = "req_start"
    REQ_CLEAR_CPLD_FAULT = "req_clear_cpld_fault"
    REQ_RESET_CPLD = "req_reset_cpld"
    REQ_RESET_DSP = "req_reset_dsp"
    REQ_ADC_RECALIBRATE = "req_adc_recalibrate"
    REQ_READ_REQUEST = "req_read_request"
    REQ_INPUT_BLOCK = "req_input_block"
    REQ_READ_THRESHOLDS = "req_read_thresholds"
    REQ_WRITE_THRESHOLDS = "req_write_thresholds"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._port: serial.Serial | None = None
        self._simulator: DspSimulator | None = None
        self._is_simulation = False
        self._running = False
        self._queue: queue.PriorityQueue = queue.PriorityQueue()
        self._seq = itertools.count()
        self._port_name = ""
        self._slave = 2
        self._baud = 115200
        self._timeout = 0.1
        self._input_quantity = INPUT_QUANTITY_V103

    @property
    def input_quantity(self) -> int:
        return self._input_quantity

    def set_input_quantity(self, qty: int):
        """设置周期轮询读取的输入寄存器数量（23 或 29）。"""
        self._input_quantity = qty

    def configure(self, port_name: str, slave: int, baud=115200, timeout=0.1, simulation=False):
        self._port_name = port_name
        self._slave = slave
        self._baud = baud
        self._timeout = timeout
        self._is_simulation = simulation or ("仿真" in port_name or port_name == "SIMULATOR")

    def run(self):
        self._running = True
        if self._is_simulation:
            self._simulator = DspSimulator(slave=self._slave)
            self.connected.emit()
            while self._running:
                self._poll_pending_sim()
                self.msleep(10)
            self._simulator = None
            self.disconnected.emit()
            return

        try:
            self._port = serial.Serial(
                port=self._port_name,
                baudrate=self._baud,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self._timeout,
            )
        except Exception as exc:
            self.port_error.emit(str(exc))
            self._running = False
            self._port = None
            return

        self.connected.emit()
        while self._running:
            self._poll_pending()
            self.msleep(2)

        if self._port and self._port.is_open:
            self._port.close()
        self._port = None
        self.disconnected.emit()

    def stop(self):
        self._running = False
        self.wait(500)

    # ---------------- 队列调度接口 ----------------

    def queue_stop(self):
        """下发 STOP 命令（最高优先级 0，剔除队列中未发送的 START 和写阈值）。"""
        with self._queue.mutex:
            self._queue.queue = [
                item for item in self._queue.queue
                if item[2] not in (self.REQ_START, self.REQ_WRITE_THRESHOLDS)
            ]
            heapq.heapify(self._queue.queue)
        self._queue.put((self.PRIO_STOP, next(self._seq), self.REQ_STOP, CMD_STOP))

    def queue_start(self):
        """下发 START 命令（优先级 2）。"""
        self._queue.put((self.PRIO_START, next(self._seq), self.REQ_START, CMD_START))

    def queue_clear_cpld_fault(self):
        """下发清除 CPLD 锁存故障命令（优先级 1，地址 0x0101，密钥 0xA55A）。"""
        self._queue.put((self.PRIO_MAINTENANCE, next(self._seq), self.REQ_CLEAR_CPLD_FAULT, KEY_CLEAR_CPLD_FAULT))

    def queue_adc_recalibrate(self):
        """请求 DSP 重新执行电流传感器零漂校准（地址 0x0104，密钥 0xCA1B）。"""
        self._queue.put((self.PRIO_MAINTENANCE, next(self._seq), self.REQ_ADC_RECALIBRATE, KEY_ADC_RECALIBRATE))

    def queue_reset_cpld(self):
        """请求 DSP 转发 CPLD 软复位命令（0x0102=0xC33C）。"""
        self._queue.put((self.PRIO_MAINTENANCE, next(self._seq), self.REQ_RESET_CPLD, KEY_RESET_CPLD))

    def queue_reset_dsp(self):
        """请求 DSP 通过看门狗软复位（0x0103=0xD55D）。"""
        self._queue.put((self.PRIO_MAINTENANCE, next(self._seq), self.REQ_RESET_DSP, KEY_RESET_DSP))

    def queue_read_thresholds(self):
        """按需读取保护阈值原始码（FC03，起始地址 0x1000，数量 3）。"""
        self._queue.put((self.PRIO_READ_HOLDING, next(self._seq), self.REQ_READ_THRESHOLDS, None))

    def queue_write_thresholds(self, vdc_raw: int, grid_raw: int, iac_raw: int):
        """按需写入 3 个保护阈值原始码（FC06 依次写入 0x1000, 0x1001, 0x1002）。"""
        self._queue.put((self.PRIO_WRITE_THRESHOLDS, next(self._seq), self.REQ_WRITE_THRESHOLDS, (vdc_raw, grid_raw, iac_raw)))

    def request_input_block(self, qty: int | None = None):
        """请求输入寄存器块（周期 FC04，优先级 20，防积压）。"""
        target_qty = qty if qty is not None else self._input_quantity
        with self._queue.mutex:
            has_input_block = any(item[2] == self.REQ_INPUT_BLOCK for item in self._queue.queue)
        if not has_input_block:
            self._queue.put((self.PRIO_READ_INPUT, next(self._seq), self.REQ_INPUT_BLOCK, target_qty))

    def request_holding_command(self):
        """请求读单个运行命令保持寄存器（FC03，优先级 10）。"""
        self._queue.put((self.PRIO_READ_HOLDING, next(self._seq), self.REQ_READ_REQUEST, None))

    def queue_write_command(self, value: int):
        """向后兼容写命令接口：0 -> STOP, 1 -> START。"""
        if value == 0:
            self.queue_stop()
        else:
            self.queue_start()

    # ---------------- 内部轮询与执行 ----------------

    def _get_next_request(self) -> tuple[str, object | None] | None:
        try:
            prio, seq, req_type, payload = self._queue.get_nowait()
            return req_type, payload
        except queue.Empty:
            return None

    def _poll_pending_sim(self):
        item = self._get_next_request()
        if item is None or not self._simulator:
            return
        req_type, payload = item

        start = time.monotonic()
        if req_type == self.REQ_INPUT_BLOCK:
            qty = int(payload) if isinstance(payload, int) else self._input_quantity
            frame = build_read_request(self._slave, 0x04, 0x0000, qty)
            resp = self._simulator.handle_request(frame)
            elapsed = (time.monotonic() - start) * 1000.0 + random.uniform(2.0, 5.0)
            parsed, values = parse_input_block(resp)
            self.reply_ready.emit(
                FrameResult(frame, resp, parsed, values, elapsed, parsed.error if parsed else None, req_type)
            )

        elif req_type in (self.REQ_STOP, self.REQ_START):
            val = CMD_STOP if req_type == self.REQ_STOP else CMD_START
            frame = build_write_single_request(self._slave, ADDR_COMMAND, val)
            resp = self._simulator.handle_request(frame)
            elapsed = (time.monotonic() - start) * 1000.0
            parsed, ok = parse_command_back(resp, val)
            err = parsed.error if parsed else "无响应"
            self.command_reply.emit(
                FrameResult(frame, resp, parsed, val if ok else None, elapsed, err if not ok else None, req_type)
            )

        elif req_type in (self.REQ_CLEAR_CPLD_FAULT, self.REQ_RESET_CPLD,
                          self.REQ_RESET_DSP, self.REQ_ADC_RECALIBRATE):
            addr_key_map = {
                self.REQ_CLEAR_CPLD_FAULT: (ADDR_CLEAR_CPLD_FAULT, KEY_CLEAR_CPLD_FAULT),
                self.REQ_RESET_CPLD: (ADDR_RESET_CPLD, KEY_RESET_CPLD),
                self.REQ_RESET_DSP: (ADDR_RESET_DSP, KEY_RESET_DSP),
                self.REQ_ADC_RECALIBRATE: (ADDR_ADC_RECALIBRATE, KEY_ADC_RECALIBRATE),
            }
            target_addr, key_val = addr_key_map[req_type]
            frame = build_write_single_request(self._slave, target_addr, key_val)
            resp = self._simulator.handle_request(frame)
            elapsed = (time.monotonic() - start) * 1000.0
            parsed, ok = parse_maintenance_back(resp, target_addr, key_val)
            err = parsed.error if parsed else "无响应"
            self.maintenance_reply.emit(
                FrameResult(frame, resp, parsed, ok, elapsed, err if not ok else None, req_type)
            )

        elif req_type == self.REQ_READ_THRESHOLDS:
            frame = build_read_request(self._slave, 0x03, ADDR_PROT_VDC_OV, 3)
            resp = self._simulator.handle_request(frame)
            elapsed = (time.monotonic() - start) * 1000.0
            parsed = parse_read_response(resp, expected_quantity=3)
            extra = parsed.registers if (parsed and parsed.registers and len(parsed.registers) == 3) else None
            self.thresholds_read_reply.emit(
                FrameResult(frame, resp, parsed, extra, elapsed, parsed.error if parsed else "无响应", req_type)
            )

        elif req_type == self.REQ_WRITE_THRESHOLDS:
            vdc_raw, grid_raw, iac_raw = payload
            targets = [
                (ADDR_PROT_VDC_OV, vdc_raw, "Vdc过压"),
                (ADDR_PROT_GRID_PEAK_OV, grid_raw, "电网峰值过压"),
                (ADDR_PROT_IAC_OC, iac_raw, "交流过流"),
            ]
            all_ok = True
            failed_addr = None
            last_resp = None
            last_parsed = None
            last_frame = b""

            for addr, val, name in targets:
                last_frame = build_write_single_request(self._slave, addr, val)
                last_resp = self._simulator.handle_request(last_frame)
                last_parsed, ok = parse_write_single_response_exact(last_resp, self._slave, addr, val)
                if not ok:
                    all_ok = False
                    failed_addr = addr
                    break

            elapsed = (time.monotonic() - start) * 1000.0
            extra_info = {
                "success": all_ok,
                "failed_addr": failed_addr,
                "exception": last_parsed.exception_code if last_parsed else None,
                "error": last_parsed.error if last_parsed else "无响应",
            }
            self.thresholds_write_reply.emit(
                FrameResult(last_frame, last_resp, last_parsed, extra_info, elapsed, last_parsed.error if not all_ok else None, req_type)
            )
            # 无论成功失败，均读回一次阈值
            self.queue_read_thresholds()

    def _poll_pending(self):
        if not self._port or not self._port.is_open:
            return
        item = self._get_next_request()
        if item is None:
            return
        req_type, payload = item

        if req_type == self.REQ_INPUT_BLOCK:
            qty = int(payload) if isinstance(payload, int) else self._input_quantity
            frame = build_read_request(self._slave, 0x04, 0x0000, qty)
            resp, elapsed, err = self._transact(frame)
            parsed, values = parse_input_block(resp)
            final_err = err or (parsed.error if parsed else None)
            self.reply_ready.emit(
                FrameResult(frame, resp, parsed, values, elapsed, final_err, req_type)
            )

        elif req_type in (self.REQ_STOP, self.REQ_START):
            val = CMD_STOP if req_type == self.REQ_STOP else CMD_START
            frame = build_write_single_request(self._slave, ADDR_COMMAND, val)
            resp, elapsed, err = self._transact(frame)
            parsed, ok = parse_command_back(resp, val)
            final_err = err or (parsed.error if parsed else None)
            self.command_reply.emit(
                FrameResult(frame, resp, parsed, val if ok else None, elapsed, final_err if not ok else None, req_type)
            )

        elif req_type in (self.REQ_CLEAR_CPLD_FAULT, self.REQ_RESET_CPLD,
                          self.REQ_RESET_DSP, self.REQ_ADC_RECALIBRATE):
            addr_key_map = {
                self.REQ_CLEAR_CPLD_FAULT: (ADDR_CLEAR_CPLD_FAULT, KEY_CLEAR_CPLD_FAULT),
                self.REQ_RESET_CPLD: (ADDR_RESET_CPLD, KEY_RESET_CPLD),
                self.REQ_RESET_DSP: (ADDR_RESET_DSP, KEY_RESET_DSP),
                self.REQ_ADC_RECALIBRATE: (ADDR_ADC_RECALIBRATE, KEY_ADC_RECALIBRATE),
            }
            target_addr, key_val = addr_key_map[req_type]
            frame = build_write_single_request(self._slave, target_addr, key_val)
            resp, elapsed, err = self._transact(frame)
            parsed, ok = parse_maintenance_back(resp, target_addr, key_val)
            final_err = err or (parsed.error if parsed else None)
            self.maintenance_reply.emit(
                FrameResult(frame, resp, parsed, ok, elapsed, final_err if not ok else None, req_type)
            )

        elif req_type == self.REQ_READ_THRESHOLDS:
            frame = build_read_request(self._slave, 0x03, ADDR_PROT_VDC_OV, 3)
            resp, elapsed, err = self._transact(frame)
            parsed = parse_read_response(resp, expected_quantity=3)
            extra = parsed.registers if (parsed and parsed.registers and len(parsed.registers) == 3) else None
            final_err = err or (parsed.error if parsed else None)
            self.thresholds_read_reply.emit(
                FrameResult(frame, resp, parsed, extra, elapsed, final_err, req_type)
            )

        elif req_type == self.REQ_WRITE_THRESHOLDS:
            vdc_raw, grid_raw, iac_raw = payload
            targets = [
                (ADDR_PROT_VDC_OV, vdc_raw),
                (ADDR_PROT_GRID_PEAK_OV, grid_raw),
                (ADDR_PROT_IAC_OC, iac_raw),
            ]
            all_ok = True
            failed_addr = None
            last_resp = None
            last_parsed = None
            last_frame = b""
            total_elapsed = 0.0

            for addr, val in targets:
                last_frame = build_write_single_request(self._slave, addr, val)
                last_resp, el, err = self._transact(last_frame)
                total_elapsed += el
                last_parsed, ok = parse_write_single_response_exact(last_resp, self._slave, addr, val)
                if not ok or err:
                    all_ok = False
                    failed_addr = addr
                    break

            extra_info = {
                "success": all_ok,
                "failed_addr": failed_addr,
                "exception": last_parsed.exception_code if last_parsed else None,
                "error": last_parsed.error if last_parsed else "无响应",
            }
            self.thresholds_write_reply.emit(
                FrameResult(last_frame, last_resp, last_parsed, extra_info, total_elapsed, last_parsed.error if not all_ok else None, req_type)
            )
            # 无论成功失败，均读回一次阈值
            self.queue_read_thresholds()

    def _transact(self, request_frame: bytes) -> tuple[bytes | None, float, str | None]:
        if not self._port or not self._port.is_open:
            return None, 0.0, "串口未打开"

        try:
            self._port.reset_input_buffer()
            self._port.reset_output_buffer()
            start = time.monotonic()
            self._port.write(request_frame)
            self._port.flush()
            response = self._read_frame()
            elapsed = (time.monotonic() - start) * 1000.0

            if not response:
                return None, elapsed, "从站超时无应答"

            if not verify_crc(response):
                return response, elapsed, "CRC 校验错误"

            time.sleep(INTER_FRAME_GAP)
            return response, elapsed, None

        except Exception as exc:
            return None, 0.0, f"串口通信异常: {exc}"

    def _read_frame(self) -> bytes:
        """带 Modbus 协议长度预测与抗分包粘包的精准收包引擎。"""
        if not self._port:
            return b""
        buf = bytearray()
        deadline = time.monotonic() + self._timeout
        expected_len = None

        while time.monotonic() < deadline:
            waiting = self._port.in_waiting
            if waiting > 0:
                chunk = self._port.read(waiting)
                buf.extend(chunk)

                # 动态根据 Modbus 协议头预测帧总长
                if expected_len is None and len(buf) >= 3:
                    func = buf[1]
                    if func & 0x80:
                        expected_len = 5  # 异常帧: slave, func|0x80, exc, crc_l, crc_h
                    elif func in (0x03, 0x04):
                        byte_count = buf[2]
                        expected_len = 3 + byte_count + 2  # 读响应帧
                    elif func in (0x06, 0x10):
                        expected_len = 8  # 写响应帧

                if expected_len is not None and len(buf) >= expected_len:
                    break
            else:
                time.sleep(0.001)

        return bytes(buf)


def parse_input_block(resp_bytes: bytes | None) -> tuple[ModbusResponse | None, tuple[int, ...] | None]:
    if not resp_bytes:
        return None, None
    parsed = parse_read_response(resp_bytes)
    return parsed, tuple(parsed.registers) if parsed.registers else None


def parse_command_back(resp_bytes: bytes | None, expected_val: int) -> tuple[ModbusResponse | None, bool]:
    if not resp_bytes:
        return None, False
    parsed = parse_write_single_response(resp_bytes)
    if parsed.error or parsed.exception_code is not None:
        return parsed, False
    return parsed, (parsed.value == expected_val)


def parse_maintenance_back(resp_bytes: bytes | None, expected_addr: int, expected_val: int) -> tuple[ModbusResponse | None, bool]:
    if not resp_bytes:
        return None, False
    parsed, ok = parse_write_single_response_exact(resp_bytes, 2, expected_addr, expected_val)
    return parsed, ok
