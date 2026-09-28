"""GUI for the dedicated EPM1270 board-test Modbus image."""
from __future__ import annotations

import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

import serial
from serial.tools import list_ports

from board_test_cli import transaction

REG_NAMES = (
    "固件版本", "有效帧计数", "电压 ADC 原始码", "电压样本（同原始码）",
    "温度脉冲/100ms", "状态", "故障高字", "原始故障",
    "运行命令", "请求计数", "总错误", "UART 错误",
    "CRC 错误", "不完整帧", "开入/开出", "有效故障/PWM",
)
EXPECTED_FIRMWARE = 0x0106

CONFIG = (
    ("故障屏蔽", 0x1100, "0x0003"),
    ("故障模拟", 0x1101, "0x0000"),
    ("开出控制(bit2=PWM)", 0x1102, "0x0004"),
    ("PWM 周期", 0x1103, "1500"),
    ("PWM 分界", 0x1104, "750"),
    ("过压阈值", 0x1000, "3410"),
)

FAULT_NAMES = (
    "旁路反馈", "对端故障输入", "上行故障", "下行通信超时",
    "硬件过压", "驱动故障 1", "驱动故障 2", "软件过压",
    "温度超限", "预留位", "接收帧错误", "温度传感器故障",
)
SOURCE_BITS = {0: 0, 1: 1, 4: 3, 5: 4, 6: 5, 8: 6, 11: 7}


def named_bits(bits: int) -> str:
    names = [name for bit, name in enumerate(FAULT_NAMES) if bits & (1 << bit)]
    return "、".join(names) if names else "无"


def fault_details(raw: int, effective: int, digital: int) -> tuple[str, str, str]:
    active, masked = [], []
    for bit, name in enumerate(FAULT_NAMES):
        if not (raw & (1 << bit)):
            continue
        if bit == 3:
            state = "当前链路超时" if not (digital & (1 << 10)) else "链路已恢复，故障锁存"
        elif bit in SOURCE_BITS:
            source_on = bool(digital & (1 << SOURCE_BITS[bit]))
            state = "当前输入有效" if source_on else "输入已恢复，故障锁存"
        else:
            state = "故障位已置位"
        description = f"{name}（{state}）"
        (active if effective & (1 << bit) else masked).append(description)
    active_text = "有效故障：" + ("；".join(active) if active else "无")
    masked_text = "当前被屏蔽的故障：" + ("；".join(masked) if masked else "无")
    input_text = (
        f"当前开入：旁路反馈={'高' if digital & 1 else '低'}，"
        f"对端故障={'高' if digital & 2 else '低'}，"
        f"备用输入={'高' if digital & 4 else '低'}；"
        f"光纤链路={'在线' if digital & (1 << 10) else '超时'}"
    )
    return active_text, masked_text, input_text


class SerialWorker(threading.Thread):
    def __init__(self, port_name: str, commands: queue.Queue, results: queue.Queue):
        super().__init__(daemon=True)
        self.port_name = port_name
        self.commands = commands
        self.results = results
        self.stop_event = threading.Event()

    def run(self):
        try:
            with serial.Serial(self.port_name, 115200, bytesize=8, parity="N",
                               stopbits=1, timeout=0.8, write_timeout=0.8) as port:
                self.results.put(("connected", self.port_name))
                while not self.stop_event.is_set():
                    try:
                        kind, address, value = self.commands.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    try:
                        if kind == "status":
                            self.results.put(("status", transaction(port, 4, 0, 16)))
                        elif kind == "config":
                            config = {address: transaction(port, 3, address, 1)[0]
                                      for address in (0x1100, 0x1101, 0x1102,
                                                      0x1103, 0x1104, 0x1000)}
                            self.results.put(("config", config))
                        elif kind == "write":
                            transaction(port, 6, address, value)
                            self.results.put(("written", (address, value)))
                    except RuntimeError as exc:
                        self.results.put(("error", str(exc)))
                        if str(exc).startswith("Modbus exception"):
                            continue
                        break
                    except Exception as exc:
                        self.results.put(("error", str(exc)))
                        break
        except Exception as exc:
            self.results.put(("error", str(exc)))
        finally:
            self.results.put(("disconnected", self.port_name))


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("SST CPLD 板卡测试上位机")
        root.geometry("980x780")
        root.minsize(820, 650)
        self.commands = queue.Queue()
        self.results = queue.Queue()
        self.worker = None
        self.status_pending = False
        self.config_pending = False
        self.connected = False

        connection = ttk.LabelFrame(root, text="光纤串口 / Modbus RTU 地址 1")
        connection.pack(fill="x", padx=12, pady=(12, 6))
        ttk.Label(connection, text="串口").pack(side="left", padx=(10, 4), pady=8)
        self.port_var = tk.StringVar()
        self.ports = ttk.Combobox(connection, textvariable=self.port_var, width=16)
        self.ports.pack(side="left", padx=4)
        ttk.Button(connection, text="刷新串口", command=self.refresh_ports).pack(
            side="left", padx=4)
        self.connect_button = ttk.Button(connection, text="连接", command=self.toggle)
        self.connect_button.pack(side="left", padx=4)
        ttk.Label(connection, text="115200 8N1").pack(side="left", padx=12)
        self.connection_label = ttk.Label(connection, text="未连接")
        self.connection_label.pack(side="right", padx=12)

        summary = ttk.LabelFrame(root, text="板卡状态")
        summary.pack(fill="x", padx=12, pady=6)
        self.firmware_var = tk.StringVar(value="固件：等待读取；PWM 互补对：PIN144/143、PIN142/141")
        ttk.Label(summary, textvariable=self.firmware_var, foreground="#0B5394").pack(
            anchor="w", padx=10, pady=(8, 0))
        self.summary_var = tk.StringVar(value="请连接光纤串口")
        ttk.Label(summary, textvariable=self.summary_var, font=("Segoe UI", 11)).pack(
            anchor="w", padx=10, pady=8)

        fault_frame = ttk.LabelFrame(root, text="故障明细（原始锁存、屏蔽与当前开入）")
        fault_frame.pack(fill="x", padx=12, pady=6)
        self.fault_var = tk.StringVar(value="等待板卡状态")
        self.masked_var = tk.StringVar(value="当前被屏蔽的故障：无")
        self.mask_config_var = tk.StringVar(value="屏蔽配置：等待板卡读回")
        self.sim_config_var = tk.StringVar(value="故障模拟配置：等待板卡读回")
        self.input_var = tk.StringVar(value="当前开入：等待数据")
        ttk.Label(fault_frame, textvariable=self.fault_var, foreground="#B00020",
                  font=("Segoe UI", 10, "bold"), wraplength=920,
                  justify="left").pack(anchor="w", padx=10, pady=(8, 3))
        ttk.Label(fault_frame, textvariable=self.masked_var, wraplength=920,
                  justify="left").pack(anchor="w", padx=10, pady=3)
        ttk.Label(fault_frame, textvariable=self.mask_config_var, wraplength=920,
                  justify="left").pack(anchor="w", padx=10, pady=3)
        ttk.Label(fault_frame, textvariable=self.sim_config_var, wraplength=920,
                  justify="left").pack(anchor="w", padx=10, pady=3)
        ttk.Label(fault_frame, textvariable=self.input_var, wraplength=920,
                  justify="left").pack(anchor="w", padx=10, pady=(3, 8))

        controls = ttk.LabelFrame(root, text="运行与保护")
        controls.pack(fill="x", padx=12, pady=6)
        for title, action in (
            ("读取状态", self.request_status),
            ("START / RUN", lambda: self.command_write(0x0100, 1)),
            ("STOP / 封锁", lambda: self.command_write(0x0100, 0)),
            ("清除已消失故障", lambda: self.command_write(0x0101, 0xA55A)),
            ("软复位", lambda: self.command_write(0x0102, 0xC33C, True)),
        ):
            ttk.Button(controls, text=title, command=action).pack(
                side="left", padx=6, pady=9)

        config_frame = ttk.LabelFrame(root, text="测试配置：仅 STOP 状态可写")
        config_frame.pack(fill="x", padx=12, pady=6)
        self.config_vars = {}
        for index, (title, address, default) in enumerate(CONFIG):
            row, column = divmod(index, 3)
            cell = ttk.Frame(config_frame)
            cell.grid(row=row, column=column, sticky="ew", padx=8, pady=6)
            ttk.Label(cell, text=f"{title} ({address:04X})").pack(anchor="w")
            var = tk.StringVar(value=default)
            self.config_vars[address] = var
            ttk.Entry(cell, textvariable=var, width=17).pack(side="left", padx=(0, 5))
            ttk.Button(cell, text="写入",
                       command=lambda a=address, v=var: self.write_config(a, v)).pack(
                           side="left")
        for column in range(3):
            config_frame.columnconfigure(column, weight=1)

        data = ttk.LabelFrame(root, text="FC04 输入寄存器（每 100 ms 自动刷新）")
        data.pack(fill="both", expand=True, padx=12, pady=6)
        self.table = ttk.Treeview(data, columns=("address", "name", "hex", "decimal"),
                                  show="headings", height=10)
        for key, title, width in (
            ("address", "地址", 75), ("name", "名称", 230),
            ("hex", "十六进制", 110), ("decimal", "十进制", 120),
        ):
            self.table.heading(key, text=title)
            self.table.column(key, width=width, anchor="center" if key != "name" else "w")
        for index, name in enumerate(REG_NAMES):
            self.table.insert("", "end", iid=str(index),
                              values=(f"{index:04X}", name, "----", ""))
        scrollbar = ttk.Scrollbar(data, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scrollbar.set)
        self.table.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        scrollbar.pack(side="right", fill="y", padx=(0, 8), pady=8)

        log_frame = ttk.LabelFrame(root, text="通信记录")
        log_frame.pack(fill="both", padx=12, pady=(6, 12))
        self.log = tk.Text(log_frame, height=5, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True, padx=8, pady=8)

        self.refresh_ports()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.drain_results)
        root.after(100, self.poll)
        root.after(500, self.poll_config)

    def refresh_ports(self):
        names = [item.device for item in list_ports.comports()]
        self.ports["values"] = names
        if names and self.port_var.get() not in names:
            self.port_var.set(names[0])

    def toggle(self):
        if self.worker and self.worker.is_alive():
            self.worker.stop_event.set()
            self.connect_button.configure(state="disabled")
            return
        port_name = self.port_var.get().strip()
        if not port_name:
            messagebox.showerror("串口", "请先选择串口")
            return
        self.commands = queue.Queue()
        self.results = queue.Queue()
        self.worker = SerialWorker(port_name, self.commands, self.results)
        self.connect_button.configure(state="disabled")
        self.worker.start()

    def request_status(self):
        if self.connected and not self.status_pending:
            self.status_pending = True
            self.commands.put(("status", 0, 16))

    def request_config(self):
        if self.connected and not self.config_pending:
            self.config_pending = True
            self.commands.put(("config", 0, 0))

    def command_write(self, address, value, confirm=False):
        if not self.connected:
            messagebox.showerror("未连接", "请先连接光纤串口")
            return
        if confirm and not messagebox.askyesno(
            "确认输出", "此操作会改变板卡运行状态。已确认功率级隔离与接线吗？"
        ):
            return
        self.commands.put(("write", address, value))

    def write_config(self, address, variable):
        try:
            value = int(variable.get().strip(), 0)
            if not 0 <= value <= 0xFFFF:
                raise ValueError
        except ValueError:
            messagebox.showerror("参数", "请输入 0～65535 的十进制数或 0x 前缀十六进制数")
            return
        self.command_write(address, value)

    def drain_results(self):
        while True:
            try:
                kind, payload = self.results.get_nowait()
            except queue.Empty:
                break
            if kind == "connected":
                self.connected = True
                self.connection_label.configure(text=f"已连接 {payload}")
                self.connect_button.configure(text="断开", state="normal")
                self.append_log(f"已连接 {payload}")
                self.request_status()
                self.request_config()
            elif kind == "status":
                self.status_pending = False
                self.show_status(payload)
            elif kind == "config":
                self.config_pending = False
                mask = payload[0x1100]
                simulated = payload[0x1101]
                self.mask_config_var.set(
                    f"屏蔽配置 0x{mask:03X}：{named_bits(mask)}")
                self.sim_config_var.set(
                    f"故障模拟配置 0x{simulated:03X}：{named_bits(simulated)}")
                for address, value in payload.items():
                    if address in (0x1100, 0x1101, 0x1102):
                        self.config_vars[address].set(f"0x{value:04X}")
                    else:
                        self.config_vars[address].set(str(value))
            elif kind == "written":
                address, value = payload
                self.append_log(f"写入 {address:04X} = {value:04X} 成功")
                self.request_status()
                self.request_config()
            elif kind == "error":
                self.status_pending = False
                self.config_pending = False
                self.append_log(f"通信错误：{payload}")
            elif kind == "disconnected":
                self.connected = False
                self.status_pending = False
                self.config_pending = False
                self.connection_label.configure(text="未连接")
                self.connect_button.configure(text="连接", state="normal")
                self.append_log("串口已断开")
        self.root.after(100, self.drain_results)

    def show_status(self, values):
        for index, value in enumerate(values):
            self.table.item(str(index), values=(
                f"{index:04X}", REG_NAMES[index], f"0x{value:04X}", str(value)))
        digital = values[14]
        active = values[15]
        run_on = bool(digital & (1 << 12))
        pwm_on = bool(digital & (1 << 13))
        pwm_note = "（1102 bit2 未开启；先 STOP 写 0x0004）" if run_on and not pwm_on else ""
        version_note = ("（本次互补 PWM 固件）" if values[0] == EXPECTED_FIRMWARE
                        else "（与本次互补 PWM 固件不符，请核对烧录文件）")
        self.firmware_var.set(
            f"固件: 0x{values[0]:04X}{version_note}；PWM 互补对：PIN144/143、PIN142/141")
        self.summary_var.set(
            f"电压 ADC 原始码: {values[2]}    温度计数: {values[4]}    "
            f"原始故障: 0x{values[7]:03X}    有效故障: 0x{active & 0x0FFF:03X}    "
            f"RUN: {int(run_on)}    PWM: {int(pwm_on)}{pwm_note}"
        )
        active_text, masked_text, input_text = fault_details(
            values[7], active & 0x0FFF, digital)
        self.fault_var.set(active_text)
        self.masked_var.set(masked_text)
        self.input_var.set(input_text)

    def poll(self):
        self.request_status()
        self.root.after(100, self.poll)

    def poll_config(self):
        self.request_config()
        self.root.after(2000, self.poll_config)

    def append_log(self, line):
        self.log.configure(state="normal")
        self.log.insert("end", time.strftime("%H:%M:%S ") + line + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def close(self):
        if self.worker:
            self.worker.stop_event.set()
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    if "--smoke-test" in sys.argv:
        from board_test_cli import crc16
        assert crc16(bytes.fromhex("010601000001")) == 0xF649
        assert len(REG_NAMES) == 16
        active, masked, inputs = fault_details(0x00B, 0x00B, 0x0403)
        assert "旁路反馈" in active and "对端故障输入" in active
        assert "下行通信超时" in active and "链路已恢复" in active
        assert "光纤链路=在线" in inputs and masked.endswith("无")
        assert named_bits(0x00B) == "旁路反馈、对端故障输入、下行通信超时"
        raise SystemExit(0)
    if "--gui-smoke-test" in sys.argv:
        root = tk.Tk()
        root.withdraw()
        App(root)
        root.update_idletasks()
        root.destroy()
        raise SystemExit(0)
    main()
