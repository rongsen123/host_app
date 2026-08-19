"""CSV 数据记录器与事件日志模块。

支持开始/停止记录实时数据为 CSV 文件，包含本机时间戳、DSP运行时间、全部原始寄存器与换算量。
"""

from __future__ import annotations

import csv
import os
import time
from pathlib import Path
from typing import Any


class DataLogger:
    """CSV 实时数据与事件记录器。"""

    CSV_HEADER = [
        "local_time",
        "dsp_uptime_ms",
        "adc_sample_count",
        "grid_halfwave_raw",
        "grid_halfwave_V",
        "iac_raw",
        "iac_zero_raw",
        "iac_A",
        "cpld_vdc_V",
        "cpld_vdc_raw",
        "cpld_vdc_average",
        "temperature_frequency_Hz",
        "temperature_count_100ms",
        "cpld_status_hex",
        "dsp_protection_hex",
        "cpld_fault_hex",
        "cpld_link_hex",
        "pll_locked",
        "pll_frequency_Hz",
        "pll_signal_valid",
        "statcom_state",
        "adc_zero_state",
        "dsp_cpld_errors",
        "pc_dsp_errors",
        "cpld_command_echo",
        "cpld_uart_error_count",
        "cpld_crc_error_count",
        "cpld_incomplete_frame_count",
        "dsp_scia_format_error_count",
        "dsp_scia_overflow_count",
        "dsp_cpld_timeout_count",
        "dsp_online",
    ]

    def __init__(self, output_dir: str = "logs"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._csv_file = None
        self._csv_writer = None
        self._is_recording = False
        self._record_count = 0
        self._current_file_path: Path | None = None

    @property
    def is_recording(self) -> bool:
        return self._is_recording

    @property
    def record_count(self) -> int:
        return self._record_count

    @property
    def current_file(self) -> str:
        return str(self._current_file_path) if self._current_file_path else ""

    def start_recording(self) -> str:
        """开始新的 CSV 录制会话，返回生成的文件路径。"""
        if self._is_recording:
            self.stop_recording()

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        self._current_file_path = self.output_dir / f"statcom_data_{timestamp}.csv"
        self._csv_file = open(self._current_file_path, mode="w", newline="", encoding="utf-8-sig")
        self._csv_writer = csv.writer(self._csv_file)
        self._csv_writer.writerow(self.CSV_HEADER)
        self._csv_file.flush()
        self._is_recording = True
        self._record_count = 0
        return str(self._current_file_path)

    def log_record(self, data: dict[str, Any], raw_values: list[int] | tuple[int, ...] | None = None):
        """写入单条采样记录。"""
        if not self._is_recording or self._csv_writer is None:
            return

        now_str = time.strftime("%Y-%m-%d %H:%M:%S.") + f"{int((time.time() % 1) * 1000):03d}"

        row = [
            now_str,
            data.get("uptime_ms", 0),
            data.get("adc_sample_count", 0),
            data.get("grid_halfwave_raw", 0),
            data.get("grid_voltage", 0.0),
            data.get("iac_raw", 0),
            data.get("iac_zero_raw", 0),
            data.get("iac", 0.0),
            data.get("cpld_vdc_v", 0.0),
            data.get("cpld_vdc_raw", 0),
            data.get("cpld_vdc_average", 0),
            data.get("temperature_frequency_hz", 0.0),
            data.get("temperature_count", 0),
            f"0x{raw_values[11]:04X}" if raw_values and len(raw_values) > 11 else "",
            f"0x{data.get('dsp_protection_raw', 0):04X}",
            f"0x{data.get('cpld_fault_raw', 0):04X}",
            f"0x{raw_values[14]:04X}" if raw_values and len(raw_values) > 14 else "",
            1 if data.get("pll_locked") else 0,
            data.get("pll_frequency", 0.0),
            1 if data.get("pll_signal_valid") else 0,
            data.get("statcom_state", ""),
            data.get("adc_zero_state", ""),
            data.get("dsp_cpld_error_count", 0),
            data.get("pc_dsp_error_count", 0),
            data.get("cpld_command_echo", ""),
            data.get("cpld_uart_error_count", 0),
            data.get("cpld_crc_error_count", 0),
            data.get("cpld_incomplete_frame_count", 0),
            data.get("dsp_scia_format_error_count", 0),
            data.get("dsp_scia_overflow_count", 0),
            data.get("dsp_cpld_timeout_count", 0),
            1 if data.get("dsp_online", True) else 0,
        ]
        self._csv_writer.writerow(row)
        self._record_count += 1
        if self._record_count % 10 == 0 and self._csv_file:
            self._csv_file.flush()

    def stop_recording(self) -> str:
        """停止录制并保存。"""
        if self._csv_file:
            self._csv_file.flush()
            self._csv_file.close()
            self._csv_file = None
        self._is_recording = False
        saved_path = str(self._current_file_path) if self._current_file_path else ""
        self._current_file_path = None
        return saved_path
