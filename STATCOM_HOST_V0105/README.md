# STATCOM_HOST_V0105 上位机监控软件

本工程为单相 STATCOM 调试监控上位机软件，基于 Python / PySide6 开发，配套协议版本 `0x0105`。

## 主要功能

- **Modbus RTU 通信**：支持 115200 bit/s、8N1，周期轮询 29 个输入寄存器（FC04）。
- **原始码换算与显示**：接收 DSP/CPLD 原始码，基于标定系数实时换算工程量；支持 `0007` 零点校准码相减。
- **保护阈值配置**：支持 STOP 状态下读写 `1000~1002` 保护阈值（FC03/FC06），并在下发前完成数值合法性与范围校验。
- **维护与复位**：支持清除故障、DSP/CPLD 远程复位及 ADC 零点重校准。
- **数据记录与导出**：支持 CSV 数据日志记录。

## 运行与测试

- **Python 版本**：Python 3.10+
- **依赖安装**：`pip install -r requirements.txt`
- **启动上位机**：`python main.py`
- **协议单元测试**：`python tests/test_protocol.py`

## 安全边界说明

界面 START/STOP 指令仅用于通信握手和状态机验收。DSP 与 CPLD 固件当前均处于功率输出硬封锁状态，不会产生实际 PWM。
