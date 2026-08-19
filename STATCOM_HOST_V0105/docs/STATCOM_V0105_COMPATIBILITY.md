# STATCOM V0105 三端系统兼容基线清单

- **发布名称**：STATCOM 4号联调基线 (V0105 原始码保护与稳定锁相)
- **协议版本**：`0x0105`
- **发布日期**：2026-08-19

## 1. 三仓提交与工程映射

| 分类 | 仓库地址 | 默认/跟踪分支 | 提交 SHA | 核心工程目录 |
| --- | --- | --- | --- | --- |
| **DSP** | `git@github.com:rongsen123/DSP_PRJ.git` | `feature/2-adc-sci-gpio-plecs` | `389f54c1ea7b4b977ac3c1fb11f6aa16f4e1379f` | `4_STATCOM_CURRENT_LOOP` |
| **CPLD** | `git@github.com:rongsen123/CPLD_PRJ.git` | `main` | `d40fe6ca735e5a2ce2b591b72e50529d33b827e8` | `CPLD_EPM1270_STATCOM_MODBUS` |
| **上位机** | `git@github.com:rongsen123/host_app.git` | `main` | `7553efd076d54cf8e42106ca897cf8fa7fc471d8` | `STATCOM_HOST_V0105` |

## 2. 工具链与开发环境

- **DSP**：Code Composer Studio 12.3.0，C2000 Compiler 22.6.0.LTS，目标器件 TMS320F28062
- **CPLD**：Altera Quartus II 13.1 (64-bit)，目标器件 MAX II EPM1270T144C5
- **上位机**：Python 3.10+，PySide6，PyInstaller 5.13+

## 3. 安全边界与硬封锁约束

- **DSP 功率封锁**：`STATCOM_POWER_OUTPUT_ENABLED=0` 保持生效，不输出 PWM。
- **CPLD 功率封锁**：4路桥臂驱动输出恒为 0，`pwm_hold_o=1`，风机与旁路继电器不动作。
- **START/STOP 行为**：上位机 START/STOP 命令仅用于通信握手与保护状态机验收，不触发任何实际功率动作。

## 4. 已验证与未完成项目

### 已验证
- 20 kHz ADC 原始码快速保护（电网峰值过压、交流瞬时过流连续3点比较确认）。
- 4 kHz 固定时基 SOGI-PLL 稳定锁定（消除队列溢出与时基抖动）。
- 双 SCI Modbus RTU 通信（DSP-CPLD 链路与 DSP-上位机链路稳定）。
- 协议 `0x0105` 单元测试通过（`test_protocol.py` 100% 通过）。
- CPLD 连续3次 Vdc 软件过压滤波与 100 ms 温度脉冲计数。

### 未完成（待后续阶段开发）
- 电流内环、电压外环控制算法。
- 功率回路实际 PWM 调制输出开放。
- 传感器工程量实物高精度标定。
