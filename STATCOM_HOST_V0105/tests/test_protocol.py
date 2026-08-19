"""STATCOM 上位机 V0x0105 原始码协议、零电流校准与保护阈值单元测试。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from communication.crc16 import crc16_modbus, append_crc, verify_crc
from communication.modbus_rtu import (
    build_read_request,
    build_write_single_request,
    parse_response,
    parse_read_response,
    parse_write_single_response_exact,
)
from communication.serial_worker import (
    DspSimulator,
    SerialWorker,
    parse_input_block,
    parse_maintenance_back,
)
from protocol.register_model import (
    ADDR_COMMAND,
    ADDR_CLEAR_CPLD_FAULT,
    ADDR_RESET_CPLD,
    ADDR_RESET_DSP,
    ADDR_ADC_RECALIBRATE,
    ADDR_PROT_VDC_OV,
    ADDR_PROT_GRID_PEAK_OV,
    ADDR_PROT_IAC_OC,
    CMD_STOP,
    CMD_START,
    KEY_CLEAR_CPLD_FAULT,
    KEY_RESET_CPLD,
    KEY_RESET_DSP,
    KEY_ADC_RECALIBRATE,
    PROT_VDC_OV_DEFAULT_RAW_V105,
    PROT_GRID_PEAK_OV_DEFAULT_RAW_V105,
    PROT_IAC_OC_DEFAULT_RAW_V105,
    PROT_VDC_OV_MAX_RAW_V105,
    PROT_GRID_PEAK_OV_MAX_RAW_V105,
    PROT_IAC_OC_MAX_RAW_V105,
    DEFAULT_K_GRID,
    DEFAULT_K_IAC,
    DEFAULT_K_VDC,
    DEFAULT_VDC_ZERO,
    PROTOCOL_VERSION_V103,
    PROTOCOL_VERSION_V104,
    PROTOCOL_VERSION_V105,
    INPUT_QUANTITY_V103,
    INPUT_QUANTITY_V101,
    calc_delta_u16,
    calc_vdc_voltage,
    calc_temperature_frequency,
    two_point_calibrate,
    parse_input_registers,
    supports_remote_maintenance,
    supports_remote_reset,
    supports_adc_recalibration,
    supports_v103_extended_registers,
    supports_protection_thresholds,
    supports_v105_raw_protocol,
    vdc_ov_eng_to_raw,
    vdc_ov_raw_to_eng,
    grid_peak_ov_eng_to_raw,
    grid_peak_ov_raw_to_eng,
    iac_oc_eng_to_raw,
    iac_oc_raw_to_eng,
    validate_protection_threshold_raw,
    to_signed,
    uint32,
)


def test_crc16_and_known_frames():
    """验证规范与交底书中列出的所有已知帧 CRC。"""
    # 读 29 个寄存器 (FC04): 02 04 00 00 00 1D -> 63 字节响应
    req_fc04_29 = build_read_request(2, 0x04, 0x0000, 29)
    assert req_fc04_29 == bytes([0x02, 0x04, 0x00, 0x00, 0x00, 0x1D, 0x30, 0x30])
    assert verify_crc(req_fc04_29) is True

    # 读 3 个保护阈值寄存器 (FC03 0x1000 x3)
    req_fc03_prot = build_read_request(2, 0x03, 0x1000, 3)
    assert verify_crc(req_fc03_prot) is True

    # 维护指令
    assert verify_crc(build_write_single_request(2, ADDR_CLEAR_CPLD_FAULT, KEY_CLEAR_CPLD_FAULT)) is True
    assert verify_crc(build_write_single_request(2, ADDR_RESET_CPLD, KEY_RESET_CPLD)) is True
    assert verify_crc(build_write_single_request(2, ADDR_RESET_DSP, KEY_RESET_DSP)) is True
    assert verify_crc(build_write_single_request(2, ADDR_ADC_RECALIBRATE, KEY_ADC_RECALIBRATE)) is True


def test_v105_raw_protocol_conversions_and_ranges():
    """测试 V0105 原始码与工程量换算、上限与范围检查。"""
    # 1. 理论换算比例系数测试
    # 1) Vdc: 974.2 V 对应 3410 count
    assert vdc_ov_eng_to_raw(974.2) == 3410
    assert abs(vdc_ov_raw_to_eng(3410) - 974.2) < 0.2

    # 2) 电网正半波峰值: 360.0 V 对应 1313 count
    assert grid_peak_ov_eng_to_raw(360.0) == 1313
    assert abs(grid_peak_ov_raw_to_eng(1313) - 360.0) < 0.2

    # 3) 交流瞬时过流: 5.00 A 对应 51 count (51 * 0.09887695 ≈ 5.04 A)
    assert iac_oc_eng_to_raw(5.00) == 51
    assert abs(iac_oc_raw_to_eng(51) - 5.04) < 0.05

    # 2. 范围校验测试
    ok, _ = validate_protection_threshold_raw(3410, 1313, 51)
    assert ok is True

    ok_low, _ = validate_protection_threshold_raw(100, 50, 10)
    assert ok_low is True

    # 超过固件原始码上限应拦截
    bad_vdc, err_vdc = validate_protection_threshold_raw(3411, 1313, 51)
    assert bad_vdc is False
    assert "Vdc" in err_vdc

    bad_grid, err_grid = validate_protection_threshold_raw(3410, 1314, 51)
    assert bad_grid is False
    assert "电网" in err_grid

    bad_iac, err_iac = validate_protection_threshold_raw(3410, 1313, 52)
    assert bad_iac is False
    assert "交流" in err_iac

    bad_zero, err_zero = validate_protection_threshold_raw(0, 1313, 51)
    assert bad_zero is False


def test_v105_0007_zero_current_subtraction():
    """测试 0006 与 0007 零电流码相减计算真实 Iac。"""
    raw_regs = [0] * 29
    raw_regs[0] = 0x0105
    # 1) 零电流测试：0006 瞬时码与 0007 零点码相同 (均为 2048) -> Iac = 0.00 A
    raw_regs[6] = 2048
    raw_regs[7] = 2048
    data = parse_input_registers(raw_regs)
    assert data["iac_raw"] == 2048
    assert data["iac_zero_raw"] == 2048
    assert abs(data["iac"] - 0.0) < 1e-4

    # 2) 正向电流测试：0006 = 2068, 0007 = 2048 -> delta = 20 -> Iac = 20 * 0.09887695 ≈ 1.98 A
    raw_regs[6] = 2068
    data_pos = parse_input_registers(raw_regs)
    assert abs(data_pos["iac"] - 1.98) < 0.05


def test_v105_simulator_fc03_fc06_and_interlocks():
    """测试仿真器 V0105 原始码 FC03/FC06 读写与 STOP 联锁。"""
    sim = DspSimulator(slave=2, protocol_version=0x0105)

    # 1. 读保护阈值 (FC03 0x1000 qty 3)
    req_read = build_read_request(2, 0x03, 0x1000, 3)
    resp_read = sim.handle_request(req_read)
    assert resp_read is not None
    parsed_read = parse_read_response(resp_read, expected_quantity=3)
    assert parsed_read.registers == [3410, 1313, 51]

    # 2. 调低阈值写入 (FC06 0x1000~0x1002)
    req_w_vdc = build_write_single_request(2, ADDR_PROT_VDC_OV, 3000)
    resp_w_vdc = sim.handle_request(req_w_vdc)
    assert resp_w_vdc == req_w_vdc
    assert sim.vdc_overvoltage_dv == 3000

    req_w_grid = build_write_single_request(2, ADDR_PROT_GRID_PEAK_OV, 1000)
    resp_w_grid = sim.handle_request(req_w_grid)
    assert resp_w_grid == req_w_grid
    assert sim.grid_peak_overvoltage_dv == 1000

    req_w_iac = build_write_single_request(2, ADDR_PROT_IAC_OC, 30)
    resp_w_iac = sim.handle_request(req_w_iac)
    assert resp_w_iac == req_w_iac
    assert sim.iac_overcurrent_ca == 30

    # 3. 超过固件上限 (3411) 写入被拒绝 (异常 0x03)
    req_bad_vdc = build_write_single_request(2, ADDR_PROT_VDC_OV, 3411)
    resp_bad = sim.handle_request(req_bad_vdc)
    parsed_bad = parse_response(resp_bad)
    assert parsed_bad.exception_code == 0x03

    # 4. START 状态下禁止写入 (异常 0x03)
    sim.command_holding = CMD_START
    resp_start_reject = sim.handle_request(build_write_single_request(2, ADDR_PROT_VDC_OV, 2500))
    parsed_reject = parse_response(resp_start_reject)
    assert parsed_reject.exception_code == 0x03


def test_dsp_and_cpld_fault_split():
    """测试 0x000C (DSP保护锁存) 与 0x000D (CPLD故障低16位) 独立拆分解析。"""
    raw_regs = [0] * 29
    raw_regs[0] = 0x0105
    raw_regs[12] = 0x0003  # 电网过压 + 交流过流
    raw_regs[13] = 0x0090  # 直流硬件过压 + 软件直流过压

    data = parse_input_registers(raw_regs)
    assert data["dsp_protection_raw"] == 0x0003
    assert data["dsp_protection"]["grid_peak_overvoltage"] is True
    assert data["dsp_protection"]["iac_instant_overcurrent"] is True
    assert data["cpld_fault_raw"] == 0x0090
    assert data["cpld_fault"]["dc_overvoltage_fault"] is True
    assert data["cpld_fault"]["software_vdc_overvoltage"] is True


def test_protocol_version_support_helpers():
    """测试协议版本能力判断函数。"""
    assert supports_remote_maintenance(0x0100) is False
    assert supports_remote_maintenance(0x0101) is True
    assert supports_protection_thresholds(0x0103) is False
    assert supports_protection_thresholds(0x0104) is True
    assert supports_v105_raw_protocol(0x0104) is False
    assert supports_v105_raw_protocol(0x0105) is True


if __name__ == "__main__":
    test_crc16_and_known_frames()
    test_v105_raw_protocol_conversions_and_ranges()
    test_v105_0007_zero_current_subtraction()
    test_v105_simulator_fc03_fc06_and_interlocks()
    test_dsp_and_cpld_fault_split()
    test_protocol_version_support_helpers()
    print("All protocol V0105 tests passed successfully!")
