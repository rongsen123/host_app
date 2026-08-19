# Changelog - STATCOM_HOST_V0105

All notable changes to the `STATCOM_HOST_V0105` host tool will be documented in this file.

## [0.1.5] - 2026-08-19

### Added
- 适配协议基线 `0x0105`，配套 4号 DSP 工程（`4_STATCOM_CURRENT_LOOP`）与 `CPLD_EPM1270_STATCOM_MODBUS`。
- 增加原始码保护阈值读写与前端自动换算（Vdc软件过压、电网峰值过压、交流瞬时过流）。
- 增加 `0007` 零电流校准码参与交流电流计算（`(0006 - 0007) * K_iac`）。
- 独立展示 DSP 快速保护状态与 CPLD 本地故障字。

### Changed
- 遥测监控由工程量接收调整为 ADC 原始码解析并在上位机端执行显示标定。
- START/STOP 增加锁定状态联锁提示；清故障功能同步支持 DSP 与 CPLD 故障复位。

### Security
- 明确提示：界面 START/STOP 仅用于通信与状态机联锁测试，下位机功率输出保持硬封锁。
