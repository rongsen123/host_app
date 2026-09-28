"""Board-test EPM1270 Modbus RTU CLI over an optical USB/serial adapter."""
import argparse
import sys

import serial

SLAVE = 1

def crc16(data: bytes) -> int:
    value = 0xFFFF
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0xA001 if value & 1 else 0)
    return value

def transaction(port: serial.Serial, function: int, address: int, value: int):
    payload = bytes((SLAVE, function, address >> 8, address & 0xFF,
                     value >> 8, value & 0xFF))
    request = payload + crc16(payload).to_bytes(2, "little")
    port.reset_input_buffer()
    port.write(request)
    head = port.read(2)
    if len(head) != 2 or head[0] != SLAVE:
        raise RuntimeError("No response from CPLD (check COM port and optical link)")
    if head[1] == function | 0x80:
        tail = port.read(3)
        frame = head + tail
        if len(frame) != 5:
            raise RuntimeError("Short Modbus exception response")
        validate_crc(frame)
        raise RuntimeError(f"Modbus exception {frame[2]:02X}")
    if head[1] != function:
        raise RuntimeError(f"Unexpected function {head[1]:02X}")
    if function == 6:
        frame = head + port.read(6)
        if len(frame) != 8:
            raise RuntimeError("Short write response")
        validate_crc(frame)
        if frame != request:
            raise RuntimeError("Write echo differs from request")
        return None
    byte_count = port.read(1)
    if len(byte_count) != 1:
        raise RuntimeError("Short read header")
    frame = head + byte_count + port.read(byte_count[0] + 2)
    if len(frame) != 3 + byte_count[0] + 2:
        raise RuntimeError("Short read response")
    validate_crc(frame)
    if byte_count[0] != value * 2:
        raise RuntimeError("Unexpected register count")
    return [int.from_bytes(frame[3+i:5+i], "big")
            for i in range(0, byte_count[0], 2)]

def validate_crc(frame: bytes):
    if crc16(frame[:-2]) != int.from_bytes(frame[-2:], "little"):
        raise RuntimeError("Bad response CRC")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("port", help="COM port of the optical adapter, e.g. COM5")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("start")
    sub.add_parser("stop")
    sub.add_parser("clear")
    sub.add_parser("soft-reset")
    rd = sub.add_parser("read")
    rd.add_argument("address", type=lambda x: int(x, 0))
    rd.add_argument("--input", action="store_true", help="FC04 instead of FC03")
    rd.add_argument("--count", type=int, default=1)
    wr = sub.add_parser("write")
    wr.add_argument("address", type=lambda x: int(x, 0))
    wr.add_argument("value", type=lambda x: int(x, 0))
    args = parser.parse_args()
    try:
        with serial.Serial(args.port, 115200, bytesize=8, parity="N",
                           stopbits=1, timeout=1.0, write_timeout=1.0) as port:
            if args.command == "status":
                r = transaction(port, 4, 0, 16)
                names = ("version", "heartbeat", "vdc_raw", "vdc_sample",
                         "temperature_count_100ms", "status", "fault_high",
                         "raw_faults", "run_echo", "valid_frames",
                         "error_frames", "uart_errors", "crc_errors",
                         "incomplete_frames", "digital_inputs",
                         "effective_faults_and_pwm")
                for name, value in zip(names, r):
                    print(f"{name:28s} 0x{value:04X} ({value})")
            elif args.command == "read":
                if args.count < 1 or args.count > (16 if args.input else 1):
                    raise ValueError("count must be 1..16 for FC04 or 1 for FC03")
                values = transaction(port, 4 if args.input else 3,
                                     args.address, args.count)
                for offset, value in enumerate(values):
                    print(f"0x{args.address+offset:04X}: 0x{value:04X} ({value})")
            else:
                writes = {"start": (0x0100, 1), "stop": (0x0100, 0),
                          "clear": (0x0101, 0xA55A),
                          "soft-reset": (0x0102, 0xC33C)}
                address, value = writes.get(args.command, (None, None))
                if args.command == "write":
                    address, value = args.address, args.value
                if not (0 <= address <= 0xFFFF and 0 <= value <= 0xFFFF):
                    raise ValueError("address and value must be 16 bit")
                transaction(port, 6, address, value)
                print(f"wrote 0x{address:04X} = 0x{value:04X}")
    except (OSError, ValueError, RuntimeError, serial.SerialException) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
