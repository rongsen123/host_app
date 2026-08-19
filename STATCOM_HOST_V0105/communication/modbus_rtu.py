"""Modbus RTU 协议帧构建与解析（纯函数，便于单元测试）。"""

from __future__ import annotations

from dataclasses import dataclass, field

from communication.crc16 import append_crc, verify_crc

# 功能码
FC_READ_HOLDING = 0x03
FC_READ_INPUT = 0x04
FC_WRITE_SINGLE = 0x06
FC_WRITE_MULTIPLE = 0x10

# 异常码
EXC_ILLEGAL_FUNCTION = 0x01
EXC_ILLEGAL_ADDRESS = 0x02
EXC_ILLEGAL_VALUE = 0x03

EXCEPTION_OFFSET = 0x80

EXCEPTION_TEXT = {
    EXC_ILLEGAL_FUNCTION: "非法功能码（固件不支持该功能码）",
    EXC_ILLEGAL_ADDRESS: "非法地址（寄存器未实现/地址错误）",
    EXC_ILLEGAL_VALUE: "非法数值或数量（或非 STOP 状态修改）",
}


@dataclass
class ModbusResponse:
    """解析后的 Modbus 响应。"""

    raw: bytes
    slave: int = 0
    function: int = 0
    data: bytes = b""
    exception_code: int | None = None
    valid_crc: bool = True
    error: str | None = None
    registers: list[int] = field(default_factory=list)
    address: int | None = None
    value: int | None = None


def build_read_request(slave: int, function: int, start_addr: int, quantity: int) -> bytes:
    """构建 FC03/FC04 读请求帧。

    :param slave: 从站地址。
    :param function: 功能码，0x03 或 0x04。
    :param start_addr: PDU 零基起始地址。
    :param quantity: 寄存器数量。
    """
    pdu = bytes([function, (start_addr >> 8) & 0xFF, start_addr & 0xFF,
                 (quantity >> 8) & 0xFF, quantity & 0xFF])
    return append_crc(bytes([slave]) + pdu)


def build_write_single_request(slave: int, address: int, value: int) -> bytes:
    """构建 FC06 写单个保持寄存器请求。"""
    pdu = bytes([FC_WRITE_SINGLE, (address >> 8) & 0xFF, address & 0xFF,
                 (value >> 8) & 0xFF, value & 0xFF])
    return append_crc(bytes([slave]) + pdu)


def build_write_multiple_request(slave: int, start_addr: int, values: list[int]) -> bytes:
    """构建 FC10 写多个保持寄存器请求。"""
    byte_count = len(values) * 2
    pdu = bytearray([
        FC_WRITE_MULTIPLE,
        (start_addr >> 8) & 0xFF,
        start_addr & 0xFF,
        (len(values) >> 8) & 0xFF,
        len(values) & 0xFF,
        byte_count,
    ])
    for v in values:
        pdu.append((v >> 8) & 0xFF)
        pdu.append(v & 0xFF)
    return append_crc(bytes([slave]) + bytes(pdu))


def parse_response(frame: bytes | None) -> ModbusResponse:
    """解析从站响应帧。

    返回 :class:`ModbusResponse`，包含校验结果与异常信息。
    """
    if frame is None:
        return ModbusResponse(raw=b"", valid_crc=False, error="无响应数据")

    resp = ModbusResponse(raw=frame)
    if len(frame) < 4:
        resp.valid_crc = False
        resp.error = "帧过短，无法解析"
        return resp

    if not verify_crc(frame):
        resp.valid_crc = False
        resp.error = "CRC 校验错误"
        return resp

    resp.slave = frame[0]
    resp.function = frame[1]

    if resp.function & EXCEPTION_OFFSET:
        exc = frame[2] if len(frame) >= 3 else 0
        resp.exception_code = exc
        resp.error = EXCEPTION_TEXT.get(exc, f"未知异常码 0x{exc:02X}")
        return resp

    resp.data = frame[2:-2]
    return resp


def parse_read_response(
    response: bytes | ModbusResponse | None,
    expected_quantity: int | None = None,
) -> ModbusResponse:
    """从 FC03/FC04 响应中解析寄存器值列表并填充至 ModbusResponse.registers。"""
    if response is None:
        return ModbusResponse(raw=b"", valid_crc=False, error="无响应数据")

    if isinstance(response, bytes):
        resp = parse_response(response)
    else:
        resp = response

    if not resp.valid_crc or resp.error or resp.exception_code is not None:
        return resp

    if resp.function not in (FC_READ_HOLDING, FC_READ_INPUT):
        resp.error = f"非预期的读功能码: 0x{resp.function:02X}"
        return resp

    if len(resp.data) < 1:
        resp.error = "响应数据区为空"
        return resp

    byte_count = resp.data[0]
    payload = resp.data[1:]
    if len(payload) != byte_count:
        resp.error = f"响应数据长度 ({len(payload)}) 与头部字节数 ({byte_count}) 不匹配"
        return resp

    if expected_quantity is not None and byte_count != expected_quantity * 2:
        resp.error = f"响应字节数 ({byte_count}) 与期望数量 ({expected_quantity * 2}) 不匹配"
        return resp

    values = []
    for i in range(0, byte_count, 2):
        values.append((payload[i] << 8) | payload[i + 1])
    resp.registers = values
    return resp


def parse_holding_read_response(response: bytes | ModbusResponse | None) -> ModbusResponse:
    """从 FC03 响应中解析单个保持寄存器值。"""
    resp = parse_read_response(response, 1)
    if resp.registers:
        resp.value = resp.registers[0]
    return resp


def parse_write_single_response(response: bytes | ModbusResponse | None) -> ModbusResponse:
    """从 FC06 响应中解析写回地址与值。"""
    if response is None:
        return ModbusResponse(raw=b"", valid_crc=False, error="无响应数据")

    if isinstance(response, bytes):
        resp = parse_response(response)
    else:
        resp = response

    if not resp.valid_crc or resp.error or resp.exception_code is not None:
        return resp

    if resp.function != FC_WRITE_SINGLE or len(resp.data) != 4:
        resp.error = "非预期的写响应格式"
        return resp

    resp.address = (resp.data[0] << 8) | resp.data[1]
    resp.value = (resp.data[2] << 8) | resp.data[3]
    return resp


def parse_write_single_response_exact(
    response: bytes | ModbusResponse | None,
    expected_slave: int,
    expected_address: int,
    expected_value: int,
) -> tuple[ModbusResponse, bool]:
    """校验 FC06 响应是否与期望的从站、地址与写入值完全一致。"""
    resp = parse_write_single_response(response)
    if not resp.valid_crc or resp.error or resp.exception_code is not None:
        return resp, False
    ok = (resp.slave == expected_slave and
          resp.address == expected_address and
          resp.value == expected_value)
    return resp, ok
