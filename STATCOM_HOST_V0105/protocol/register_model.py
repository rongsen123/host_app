"""寄存器模型：将原始寄存器值解析为物理量、状态位与枚举文本。

数据来源依据《单相STATCOM上位机 V0x0105 原始码协议修改交底书》与最新电路理论比例。
"""

from __future__ import annotations

# 输入寄存器数量
INPUT_QUANTITY_V103 = 29
INPUT_QUANTITY_V101 = 23
INPUT_QUANTITY = INPUT_QUANTITY_V103
INPUT_QUANTITY_MIN = INPUT_QUANTITY_V101

# 协议版本常量
MIN_MAINTENANCE_PROTOCOL_VERSION = 0x0101
MIN_RESET_PROTOCOL_VERSION = 0x0102
MIN_ADC_RECALIBRATION_PROTOCOL_VERSION = 0x0102
PROTOCOL_VERSION_V103 = 0x0103
PROTOCOL_VERSION_V104 = 0x0104
PROTOCOL_VERSION_V105 = 0x0105

# 保持寄存器地址与操作密钥
ADDR_COMMAND = 0x0100
CMD_STOP = 0x0000
CMD_START = 0x0001

ADDR_CLEAR_CPLD_FAULT = 0x0101
KEY_CLEAR_CPLD_FAULT = 0xA55A

ADDR_RESET_CPLD = 0x0102
KEY_RESET_CPLD = 0xC33C

ADDR_RESET_DSP = 0x0103
KEY_RESET_DSP = 0xD55D

ADDR_ADC_RECALIBRATE = 0x0104
KEY_ADC_RECALIBRATE = 0xCA1B

# V0105 保护阈值保持寄存器 (FC03 读 / FC06 写，设备直接传输与保存原始码)
ADDR_PROT_VDC_OV = 0x1000        # CPLD Vdc 软件过压绝对原始码 (ADC count, 1..3410)
ADDR_PROT_GRID_PEAK_OV = 0x1001  # DSP 电网正半波过压绝对原始码 (ADC count, 1..1313)
ADDR_PROT_IAC_OC = 0x1002        # DSP 交流电流瞬时过流偏差原始码 abs(raw-zero) (ADC count, 1..51)

# V0105 原始码上限与默认值
PROT_VDC_OV_MAX_RAW_V105 = 3410       # 3410 count (约 974.2 V)
PROT_GRID_PEAK_OV_MAX_RAW_V105 = 1313 # 1313 count (约 360.0 V)
PROT_IAC_OC_MAX_RAW_V105 = 51         # 51 count (约 5.04 A)

PROT_VDC_OV_DEFAULT_RAW_V105 = 3410
PROT_GRID_PEAK_OV_DEFAULT_RAW_V105 = 1313
PROT_IAC_OC_DEFAULT_RAW_V105 = 51

# 物理量工程转换默认比例系数 (支持用户/实物标定更新)
DEFAULT_K_GRID = 0.2741878    # V/count
DEFAULT_K_IAC = 0.09887695    # A/count
DEFAULT_K_VDC = 0.28568177    # V/count (K = 5*4604.6 / (4095*8.2*2.4) ≈ 0.28568177)
DEFAULT_VDC_GAIN = DEFAULT_K_VDC
DEFAULT_VDC_ZERO = 0.0


def two_point_calibrate(c1: float, v1: float, c2: float, v2: float) -> tuple[float, float]:
    """两点标定计算增益与零点码：V = (C - zero_code) * gain。"""
    if abs(c2 - c1) < 1e-6:
        raise ValueError("标定两点 ADC 码值不能相同")
    gain = (v2 - v1) / (c2 - c1)
    zero_code = c1 - (v1 / gain) if abs(gain) > 1e-6 else 0.0
    return gain, zero_code


def calc_vdc_voltage(raw_count: int | float, gain: float = DEFAULT_K_VDC, zero_code: float = DEFAULT_VDC_ZERO) -> float:
    """按标定参数将 CPLD Vdc ADC 码换算为直流电压 (V)：Vdc = max(0, (raw_count - zero_code) * gain)。"""
    v = (float(raw_count) - float(zero_code)) * float(gain)
    return max(0.0, v)


def calc_temperature_frequency(count: int | float) -> float:
    """将 100ms 窗口内的脉冲计数换算为频率 (Hz)。"""
    return float(count) * 10.0


def calc_delta_u16(current: int, previous: int) -> int:
    """计算 16 位无符号累加计数器的增量，正确处理回绕。"""
    return (current - previous) & 0xFFFF


# V0105 保护阈值原始码与工程量双向换算
def vdc_ov_eng_to_raw(v: float, k_vdc: float = DEFAULT_K_VDC, zero_raw: float = DEFAULT_VDC_ZERO) -> int:
    """用户输入 Vdc (V) -> 寄存器原始码 (int): round((V / Kvdc) + zero_raw)"""
    if k_vdc <= 1e-6:
        return 0
    return int(round((v / k_vdc) + zero_raw))


def vdc_ov_raw_to_eng(raw: int, k_vdc: float = DEFAULT_K_VDC, zero_raw: float = DEFAULT_VDC_ZERO) -> float:
    """寄存器原始码 -> Vdc (V): max(0, (raw - zero_raw) * Kvdc)"""
    return round(max(0.0, (float(raw) - float(zero_raw)) * float(k_vdc)), 1)


def grid_peak_ov_eng_to_raw(v: float, k_grid: float = DEFAULT_K_GRID) -> int:
    """用户输入电网峰值 (V) -> 寄存器原始码 (int): round(V / Kgrid)"""
    if k_grid <= 1e-6:
        return 0
    return int(round(v / k_grid))


def grid_peak_ov_raw_to_eng(raw: int, k_grid: float = DEFAULT_K_GRID) -> float:
    """寄存器原始码 -> 电网峰值 (V): raw * Kgrid"""
    return round(float(raw) * float(k_grid), 1)


def iac_oc_eng_to_raw(a: float, k_iac: float = DEFAULT_K_IAC) -> int:
    """用户输入交流过流 (A) -> 寄存器原始偏差码 (int): round(A / Kiac)"""
    if k_iac <= 1e-6:
        return 0
    return int(round(a / k_iac))


def iac_oc_raw_to_eng(raw: int, k_iac: float = DEFAULT_K_IAC) -> float:
    """寄存器原始偏差码 -> 交流过流 (A): raw * Kiac"""
    return round(float(raw) * float(k_iac), 2)


def validate_protection_threshold_raw(
    vdc_raw: int, grid_raw: int, iac_raw: int
) -> tuple[bool, str]:
    """校验三个保护阈值原始寄存器值是否处于固件允许的安全范围 (1..上限)。"""
    if not (1 <= vdc_raw <= PROT_VDC_OV_MAX_RAW_V105):
        max_v = vdc_ov_raw_to_eng(PROT_VDC_OV_MAX_RAW_V105)
        return False, f"CPLD Vdc 过压原始码超出范围：需在 1 ~ {PROT_VDC_OV_MAX_RAW_V105} count (对应约 0.1 ~ {max_v} V)"
    if not (1 <= grid_raw <= PROT_GRID_PEAK_OV_MAX_RAW_V105):
        max_grid = grid_peak_ov_raw_to_eng(PROT_GRID_PEAK_OV_MAX_RAW_V105)
        return False, f"DSP 电网峰值过压原始码超出范围：需在 1 ~ {PROT_GRID_PEAK_OV_MAX_RAW_V105} count (对应约 0.1 ~ {max_grid} V)"
    if not (1 <= iac_raw <= PROT_IAC_OC_MAX_RAW_V105):
        max_iac = iac_oc_raw_to_eng(PROT_IAC_OC_MAX_RAW_V105)
        return False, f"DSP 交流过流偏差原始码超出范围：需在 1 ~ {PROT_IAC_OC_MAX_RAW_V105} count (对应约 0.01 ~ {max_iac} A)"
    return True, ""


# 协议能力判断
def supports_remote_maintenance(protocol_version: int) -> bool:
    """判断固件版本是否支持远程清除 CPLD 锁存故障。"""
    return (protocol_version & 0xFFFF) >= MIN_MAINTENANCE_PROTOCOL_VERSION


def supports_adc_recalibration(protocol_version: int) -> bool:
    """判断固件版本是否支持 STOP 联锁的 ADC 重新零漂校准。"""
    return (protocol_version & 0xFFFF) >= MIN_ADC_RECALIBRATION_PROTOCOL_VERSION


def supports_remote_reset(protocol_version: int) -> bool:
    """判断固件版本是否支持 0x0102/0x0103 受控复位。"""
    return (protocol_version & 0xFFFF) >= MIN_RESET_PROTOCOL_VERSION


def supports_v103_extended_registers(protocol_version: int) -> bool:
    """判断固件版本是否支持 29 个输入寄存器扩展诊断。"""
    return (protocol_version & 0xFFFF) >= PROTOCOL_VERSION_V103


def supports_protection_thresholds(protocol_version: int) -> bool:
    """判断固件版本是否支持保护阈值读写 (0x1000~0x1002)。"""
    return (protocol_version & 0xFFFF) >= PROTOCOL_VERSION_V104


def supports_v105_raw_protocol(protocol_version: int) -> bool:
    """判断固件版本是否支持 V0x0105 原始码协议。"""
    return (protocol_version & 0xFFFF) >= PROTOCOL_VERSION_V105


# 枚举表
COMMANDS = {0: "STOP", 1: "START"}
ADC_ZERO_STATES = {0: "IDLE", 1: "RUNNING", 2: "DONE"}
STATCOM_STATES = {
    0: "SAFE_BOOT",
    1: "ADC_VERIFY",
    2: "LINK_VERIFY",
    3: "PLL_MONITOR",
    4: "CONTROL_SHADOW",
    5: "CURRENT_LOOP",
    6: "VOLTAGE_LOOP",
    7: "RUN",
    8: "FAULT",
}

# 位域定义
CPLD_STATUS_BITS = {
    0: "link_online",
    1: "adc_valid",
    2: "temperature_valid",
    3: "fault_any",
    4: "pwm_healthy",
}

CPLD_LINK_BITS = {
    0: "online",
    1: "timeout_offline",
    2: "crc_error_seen",
    3: "exception_seen",
}

# V0104/V0105: 0x000C DSP 保护锁存位域映射
DSP_PROTECTION_BITS = {
    0: "grid_peak_overvoltage",
    1: "iac_instant_overcurrent",
    2: "pll_loss",
    3: "cpld_link_fault",
    4: "cpld_reported_fault",
}

# V0104/V0105: 0x000D CPLD 硬件故障低16位映射
CPLD_FAULT_BITS_V104 = {
    0: "bypass_status",
    1: "peer_module_fault",
    4: "dc_overvoltage_fault",
    5: "drive_fault_1",
    6: "drive_fault_2",
    7: "software_vdc_overvoltage",
    8: "temperature_over",
    10: "cpld_rx_error_latch",
    11: "temperature_sensor_fault",
    12: "precharge_under",
    13: "precharge_over",
    14: "precharge_timeout",
    15: "adc_stale",
}

# 兼容旧版本的完整位域映射
CPLD_FAULT_BITS = {
    0: "bypass_status",
    1: "peer_module_fault",
    4: "dc_overvoltage_fault",
    5: "drive_fault_1",
    6: "drive_fault_2",
    7: "software_vdc_overvoltage",
    8: "temperature_over",
    10: "cpld_rx_error_latch",
    11: "temperature_sensor_fault",
    12: "precharge_under",
    13: "precharge_over",
    14: "precharge_timeout",
    15: "adc_stale",
    18: "pwm_monitor_fault",
    19: "dsp_cpld_link_timeout",
    22: "config_invalid",
}


def to_signed(v16: int) -> int:
    """将 16 位无符号寄存器值转换为有符号整数。"""
    v = v16 & 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def uint32(high: int, low: int) -> int:
    """高位字在前组合 32 位无符号整数。"""
    return ((high & 0xFFFF) << 16) | (low & 0xFFFF)


def decode_bitfield(value: int, bit_map: dict[int, str]) -> dict[str, bool]:
    """按位域映射表解码，返回 {名称: 是否置位}。"""
    out = {name: False for name in bit_map.values()}
    for bit, name in bit_map.items():
        out[name] = bool(value & (1 << bit))
    return out


# 29 个输入寄存器的标准定义元数据表 (V0105 原始码协议版)
REGISTER_DEFINITIONS: list[dict] = [
    {
        "addr": 0x0000,
        "name": "DSP 协议版本号",
        "category": "系统信息",
        "unit": "hex",
        "desc": "上位机与 DSP 通信协议版本 (如 0x0105)",
    },
    {
        "addr": 0x0001,
        "name": "DSP 运行时间 (高16位)",
        "category": "系统信息",
        "unit": "ms",
        "desc": "系统上电/复位后累计运行时间的高字 (单位: 毫秒)",
    },
    {
        "addr": 0x0002,
        "name": "DSP 运行时间 (低16位)",
        "category": "系统信息",
        "unit": "ms",
        "desc": "系统累计运行时间的低字，组合构成 32 位时间戳",
    },
    {
        "addr": 0x0003,
        "name": "ADC 中断采样计数 (高16位)",
        "category": "采样统计",
        "unit": "count",
        "desc": "EPWM 触发 ADC 中断的总累加采样次数高字",
    },
    {
        "addr": 0x0004,
        "name": "ADC 中断采样计数 (低16位)",
        "category": "采样统计",
        "unit": "count",
        "desc": "EPWM 触发 ADC 中断的总累加采样次数低字",
    },
    {
        "addr": 0x0005,
        "name": "电网电压正半波绝对原始码 (ADCINA0)",
        "category": "电网遥测",
        "unit": "count",
        "desc": "电网电压正半波整流瞬时 ADC 码 (0~4095)，Vgrid = raw * Kgrid",
    },
    {
        "addr": 0x0006,
        "name": "交流电流瞬时绝对原始码 (ADCINA2)",
        "category": "电流遥测",
        "unit": "count",
        "desc": "STATCOM 交流电流瞬时未校零 ADC 码 (0~4095)",
    },
    {
        "addr": 0x0007,
        "name": "ADCINA2 零电流校准原始码",
        "category": "电流遥测",
        "unit": "count",
        "desc": "自校准零电流偏置 ADC 码，Iac = (0006 - 0007) * Kiac",
    },
    {
        "addr": 0x0008,
        "name": "CPLD Vdc 最新原始码 (ADS7818)",
        "category": "直流母线",
        "unit": "count",
        "desc": "CPLD 端 ADS7818 采集的母线电压瞬时 12 位原始码",
    },
    {
        "addr": 0x0009,
        "name": "CPLD Vdc 256点平均原始码",
        "category": "直流母线",
        "unit": "count",
        "desc": "CPLD 硬件 256 点滑动滤波平均码，Vdc = (raw - zero) * Kvdc",
    },
    {
        "addr": 0x000A,
        "name": "IGBT 温度脉冲计数",
        "category": "温度监控",
        "unit": "count/100ms",
        "desc": "100ms 窗口内脉冲数，频率 f = count * 10 Hz；CPLD 硬件保护门限 5000 count/100ms",
    },
    {
        "addr": 0x000B,
        "name": "CPLD 硬件状态位域",
        "category": "CPLD状态",
        "unit": "bitfield",
        "desc": "Bit0链路在线、Bit1 ADC有效、Bit2温度有效、Bit3总故障、Bit4 PWM健康",
    },
    {
        "addr": 0x000C,
        "name": "DSP 保护锁存状态位域",
        "category": "DSP保护",
        "unit": "bitfield",
        "desc": "Bit0电网过压, Bit1交流过流, Bit2失锁, Bit3链路离线, Bit4 CPLD故障",
    },
    {
        "addr": 0x000D,
        "name": "CPLD 硬件故障低16位",
        "category": "CPLD故障",
        "unit": "bitfield",
        "desc": "Bit0旁路, Bit1对端故障, Bit4-6驱动/硬件过压, Bit7软过压, Bit8过温, Bit10通信错等",
    },
    {
        "addr": 0x000E,
        "name": "DSP-CPLD 链路状态位域",
        "category": "链路诊断",
        "unit": "bitfield",
        "desc": "Bit0当前在线、Bit1 500ms超时离线、Bit2 CRC历史错误、Bit3 异常响应",
    },
    {
        "addr": 0x000F,
        "name": "SOGI-PLL 锁相环锁定标志",
        "category": "锁相环",
        "unit": "bool",
        "desc": "0: 未锁定, 1: 已成功锁定电网基波相位与频率",
    },
    {
        "addr": 0x0010,
        "name": "SOGI-PLL 电网频率",
        "category": "锁相环",
        "unit": "0.01 Hz",
        "desc": "锁相环跟踪的电网实时基波频率 (如 5000 代表 50.00 Hz)",
    },
    {
        "addr": 0x0011,
        "name": "SOGI-PLL 输入信号有效标志",
        "category": "锁相环",
        "unit": "bool",
        "desc": "0: 电网正半波异常/无信号, 1: 信号幅值有效",
    },
    {
        "addr": 0x0012,
        "name": "STATCOM 主控运行状态",
        "category": "主控制",
        "unit": "enum",
        "desc": "0:SAFE_BOOT, 1:ADC_VERIFY, 3:PLL_MONITOR, 7:RUN, 8:FAULT 等",
    },
    {
        "addr": 0x0013,
        "name": "ADC 电流零漂自校准状态",
        "category": "自校准",
        "unit": "enum",
        "desc": "0: IDLE(未校准), 1: RUNNING(1024点采样中), 2: DONE(校准完成)",
    },
    {
        "addr": 0x0014,
        "name": "DSP-CPLD 通信综合错误计数",
        "category": "通信诊断",
        "unit": "次",
        "desc": "DSP 观察到的 SCI-A 错误总数 (含 CRC/格式/超时/异常)",
    },
    {
        "addr": 0x0015,
        "name": "PC-DSP 通信累计错误计数",
        "category": "通信诊断",
        "unit": "次",
        "desc": "DSP 接收上位机 SCI-B 帧时捕获的 CRC/格式错误累计",
    },
    {
        "addr": 0x0016,
        "name": "CPLD 命令回显状态",
        "category": "主控制",
        "unit": "enum",
        "desc": "0: STOP (安全停机), 1: START (调试运行回显)",
    },
    {
        "addr": 0x0017,
        "name": "CPLD UART 停止位错误计数",
        "category": "CPLD接收",
        "unit": "次",
        "desc": "CPLD 接收 DSP 串口帧时因停止位未拉高产生的 Framing Error",
    },
    {
        "addr": 0x0018,
        "name": "CPLD Modbus CRC 错误计数",
        "category": "CPLD接收",
        "unit": "次",
        "desc": "CPLD 收到请求帧但 CRC16 校验不匹配的累计次数",
    },
    {
        "addr": 0x0019,
        "name": "CPLD t3.5 残帧超时计数",
        "category": "CPLD接收",
        "unit": "次",
        "desc": "CPLD 接收字节未达一帧且间隔超过 1.75ms 的丢弃残帧数",
    },
    {
        "addr": 0x001A,
        "name": "DSP SCI-A 硬件格式错误",
        "category": "DSP接收",
        "unit": "次",
        "desc": "DSP 硬件捕获的校验/溢出/帧格式错误 (PE/OE/FE/BRKDT/RXERROR)",
    },
    {
        "addr": 0x001B,
        "name": "DSP SCI-A 接收溢出计数",
        "category": "DSP接收",
        "unit": "次",
        "desc": "DSP 硬件 FIFO 或内部软件环形缓冲区满溢出的丢包计数",
    },
    {
        "addr": 0x001C,
        "name": "DSP 等待 CPLD 回复超时计数",
        "category": "DSP接收",
        "unit": "次",
        "desc": "DSP 发出读/写请求后 20ms 内未收到 CPLD 响应的超时重试数",
    },
]


def parse_input_registers(
    values: list[int] | tuple[int, ...],
    vdc_gain: float = DEFAULT_K_VDC,
    vdc_zero: float = DEFAULT_VDC_ZERO,
    k_grid: float = DEFAULT_K_GRID,
    k_iac: float = DEFAULT_K_IAC,
) -> dict:
    """解析输入寄存器的原始值，返回结构化字典（V0105 原始码协议版）。"""
    if len(values) < INPUT_QUANTITY_MIN:
        raise ValueError(f"输入寄存器数量不足：最少需 {INPUT_QUANTITY_MIN}，实得 {len(values)}")

    v = values
    data: dict = {}

    # 协议与计时 (0x0000 ~ 0x0004)
    data["protocol_version"] = v[0]
    data["uptime_ms"] = uint32(v[1], v[2])
    data["adc_sample_count"] = uint32(v[3], v[4])

    # 模拟量 (0x0005 ~ 0x000A) - V0105 原始码换算
    data["grid_halfwave_raw"] = v[5]
    data["grid_voltage"] = round(v[5] * k_grid, 2)  # Vgrid = raw * Kgrid

    data["iac_raw"] = v[6]
    data["iac_zero_raw"] = v[7]
    # V0105: 0007 为零电流校准原始码，Iac = (0006 - 0007) * Kiac
    data["iac"] = round((v[6] - v[7]) * k_iac, 2)

    data["cpld_vdc_raw"] = v[8]
    data["cpld_vdc_average"] = v[9]
    data["cpld_vdc_v"] = round(calc_vdc_voltage(v[9], vdc_gain, vdc_zero), 1)

    data["temperature_count"] = v[10]
    data["temperature_frequency_hz"] = round(calc_temperature_frequency(v[10]), 1)

    # 状态位 (0x000B ~ 0x000E)
    data["cpld_status"] = decode_bitfield(v[11], CPLD_STATUS_BITS)

    # 0x000C 为 DSP 保护锁存位，0x000D 为 CPLD 故障低16位
    dsp_prot_raw = v[12]
    data["dsp_protection_raw"] = dsp_prot_raw
    data["dsp_protection"] = decode_bitfield(dsp_prot_raw, DSP_PROTECTION_BITS)

    cpld_fault_raw = v[13]
    data["cpld_fault_raw"] = cpld_fault_raw
    data["cpld_fault"] = decode_bitfield(cpld_fault_raw, CPLD_FAULT_BITS_V104)

    data["cpld_link"] = decode_bitfield(v[14], CPLD_LINK_BITS)

    # 锁相环状态 (0x000F ~ 0x0011)
    data["pll_locked"] = bool(v[15])
    data["pll_frequency"] = round(v[16] * 0.01, 2)  # 单位 Hz
    data["pll_signal_valid"] = bool(v[17])

    # 系统状态 (0x0012 ~ 0x0016)
    data["statcom_state_raw"] = v[18]
    data["statcom_state"] = STATCOM_STATES.get(v[18], f"0x{v[18]:04X}")
    data["adc_zero_state_raw"] = v[19]
    data["adc_zero_state"] = ADC_ZERO_STATES.get(v[19], f"0x{v[19]:04X}")

    data["dsp_cpld_error_count"] = v[20]
    data["pc_dsp_error_count"] = v[21]
    data["cpld_command_echo_raw"] = v[22]
    data["cpld_command_echo"] = COMMANDS.get(v[22], f"0x{v[22]:04X}")

    # 扩展诊断寄存器 (0x0017 ~ 0x001C, V0x0103/0x0104/0x0105)
    if len(values) >= INPUT_QUANTITY_V103:
        data["cpld_uart_error_count"] = v[23]
        data["cpld_crc_error_count"] = v[24]
        data["cpld_incomplete_frame_count"] = v[25]
        data["dsp_scia_format_error_count"] = v[26]
        data["dsp_scia_overflow_count"] = v[27]
        data["dsp_cpld_timeout_count"] = v[28]
        data["has_extended_diag"] = True
    else:
        data["cpld_uart_error_count"] = 0
        data["cpld_crc_error_count"] = 0
        data["cpld_incomplete_frame_count"] = 0
        data["dsp_scia_format_error_count"] = 0
        data["dsp_scia_overflow_count"] = 0
        data["dsp_cpld_timeout_count"] = 0
        data["has_extended_diag"] = False

    # 汇总故障
    dsp_fault_active = (dsp_prot_raw != 0)
    cpld_fault_active = (cpld_fault_raw != 0) or data["cpld_status"].get("fault_any", False)
    data["fault_active"] = dsp_fault_active or cpld_fault_active
    data["dsp_fault_active"] = dsp_fault_active
    data["cpld_fault_active"] = cpld_fault_active
    return data


def format_register_interpreted_value(
    addr: int,
    raw_val: int,
    all_raw: list[int] | None = None,
    vdc_gain: float = DEFAULT_K_VDC,
    vdc_zero: float = DEFAULT_VDC_ZERO,
    k_grid: float = DEFAULT_K_GRID,
    k_iac: float = DEFAULT_K_IAC,
) -> str:
    """根据寄存器地址与原始值，生成便于直观理解的物理/状态解释文本。"""
    if addr == 0x0000:
        return f"V{raw_val >> 8}.{raw_val & 0xFF:02X} (0x{raw_val:04X})"
    if addr in (0x0001, 0x0002):
        if all_raw and len(all_raw) > 2:
            ms = uint32(all_raw[1], all_raw[2])
            return f"总时长: {ms/1000.0:.1f}s"
        return f"{raw_val} ms"
    if addr in (0x0003, 0x0004):
        if all_raw and len(all_raw) > 4:
            cnt = uint32(all_raw[3], all_raw[4])
            return f"总采样: {cnt} 次"
        return f"{raw_val}"
    if addr == 0x0005:
        v = raw_val * k_grid
        return f"{v:.1f} V (原始码: {raw_val})"
    if addr == 0x0006:
        if all_raw and len(all_raw) > 7:
            zero_cnt = all_raw[7]
            iac_val = (raw_val - zero_cnt) * k_iac
            return f"瞬时: {raw_val} (Iac: {iac_val:+.2f} A)"
        return f"原始码: {raw_val}"
    if addr == 0x0007:
        return f"零点偏置码: {raw_val}"
    if addr == 0x0008:
        v = calc_vdc_voltage(raw_val, vdc_gain, vdc_zero)
        return f"{v:.1f} V (瞬时码: {raw_val})"
    if addr == 0x0009:
        v = calc_vdc_voltage(raw_val, vdc_gain, vdc_zero)
        return f"{v:.1f} V (平均码: {raw_val})"
    if addr == 0x000A:
        f = calc_temperature_frequency(raw_val)
        return f"{raw_val} count/100ms ({f:.0f} Hz)"
    if addr == 0x000B:
        st = decode_bitfield(raw_val, CPLD_STATUS_BITS)
        online = "在线" if st["link_online"] else "离线"
        fault = "有故障" if st["fault_any"] else "无故障"
        return f"CPLD: {online} / {fault}"
    if addr == 0x000C:
        if raw_val == 0:
            return "✅ 0x0000 (DSP 保护正常)"
        active_bits = [name for bit, name in DSP_PROTECTION_BITS.items() if raw_val & (1 << bit)]
        return f"⚠️ 0x{raw_val:04X} ({', '.join(active_bits)})"
    if addr == 0x000D:
        if raw_val == 0:
            return "✅ 0x0000 (CPLD 硬件正常)"
        return f"⚠️ 0x{raw_val:04X} (CPLD 故障触发)"
    if addr == 0x000E:
        lk = decode_bitfield(raw_val, CPLD_LINK_BITS)
        return "在线" if lk["online"] else ("超时离线" if lk["timeout_offline"] else "未知")
    if addr == 0x000F:
        return "✅ 已锁定" if raw_val == 1 else "❌ 未锁定"
    if addr == 0x0010:
        return f"{raw_val * 0.01:.2f} Hz"
    if addr == 0x0011:
        return "✅ 信号有效" if raw_val == 1 else "❌ 信号无效"
    if addr == 0x0012:
        return STATCOM_STATES.get(raw_val, f"0x{raw_val:04X}")
    if addr == 0x0013:
        return ADC_ZERO_STATES.get(raw_val, f"0x{raw_val:04X}")
    if addr == 0x0014:
        return f"{raw_val} 次 (DSP-CPLD综合错误)"
    if addr == 0x0015:
        return f"{raw_val} 次 (PC-DSP综合错误)"
    if addr == 0x0016:
        return COMMANDS.get(raw_val, f"0x{raw_val:04X}")
    if addr == 0x0017:
        return f"{raw_val} 次 (停止位错误)"
    if addr == 0x0018:
        return f"{raw_val} 次 (CRC错误)"
    if addr == 0x0019:
        return f"{raw_val} 次 (残帧超时)"
    if addr == 0x001A:
        return f"{raw_val} 次 (硬件格式错误)"
    if addr == 0x001B:
        return f"{raw_val} 次 (接收溢出)"
    if addr == 0x001C:
        return f"{raw_val} 次 (等待回复超时)"
    return f"0x{raw_val:04X}"


def active_fault_names(data: dict) -> list[str]:
    """返回当前置位故障与保护的友好名称列表。"""
    dsp_map = {
        "grid_peak_overvoltage": "DSP 电网峰值软件过压",
        "iac_instant_overcurrent": "DSP 交流电流瞬时软件过流",
        "pll_loss": "DSP PLL 曾锁定后失锁",
        "cpld_link_fault": "DSP-CPLD 通信离线",
        "cpld_reported_fault": "CPLD 上报故障",
    }
    cpld_map = {
        "bypass_status": "旁路状态",
        "peer_module_fault": "相邻模块故障",
        "dc_overvoltage_fault": "直流硬件过压",
        "drive_fault_1": "驱动故障 1",
        "drive_fault_2": "驱动故障 2",
        "software_vdc_overvoltage": "软件直流过压",
        "temperature_over": "温度过高(频率超5000Hz)",
        "cpld_rx_error_latch": "CPLD通信接收错误锁存",
        "temperature_sensor_fault": "温度传感器无脉冲故障",
        "precharge_under": "预充电欠压",
        "precharge_over": "预充电过压",
        "precharge_timeout": "预充电超时",
        "adc_stale": "ADC 数据过期",
    }
    res = []
    for k, v in data.get("dsp_protection", {}).items():
        if v:
            res.append(dsp_map.get(k, k))
    for k, v in data.get("cpld_fault", {}).items():
        if v and k not in ("bypass_status",):
            res.append(cpld_map.get(k, k))
    return res
