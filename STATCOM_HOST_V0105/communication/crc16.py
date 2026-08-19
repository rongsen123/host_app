"""CRC16/Modbus 校验工具。

规范要求：
- 初值 0xFFFF
- 多项式 0xA001
- 低字节先发
"""


def crc16_modbus(data: bytes) -> int:
    """计算 Modbus RTU 帧的 CRC16。

    :param data: 待计算的字节串（不含 CRC）。
    :return: 16 位 CRC 值，发送时低字节在前。
    """
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def crc_low_high(data: bytes) -> tuple[int, int]:
    """返回 (low, high) 两个字节，符合 Modbus RTU 低字节先发规则。"""
    crc = crc16_modbus(data)
    return crc & 0xFF, (crc >> 8) & 0xFF


def append_crc(data: bytes) -> bytes:
    """在数据末尾追加 CRC 并返回完整帧。"""
    low, high = crc_low_high(data)
    return data + bytes([low, high])


def verify_crc(frame: bytes) -> bool:
    """校验完整帧（含 CRC）是否正确，CRC 错误时返回 ``False``。"""
    if len(frame) < 4:
        return False
    payload, rx_crc = frame[:-2], frame[-2:]
    calc = crc_low_high(payload)
    return calc == (rx_crc[0], rx_crc[1])
