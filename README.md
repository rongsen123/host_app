# host_app 上位机工程仓库

本仓库用于统一管理 SST 项目配套的各类上位机监控与调试软件。采用“一个上位机一个文件夹”的多工程架构。

## 工程目录

详见 [PROJECT_INDEX.md](PROJECT_INDEX.md)。

| 目录 | 适配设备 | 协议版本 | 语言/框架 | 配套固件 |
| --- | --- | --- | --- | --- |
| [STATCOM_HOST_V0105](STATCOM_HOST_V0105/) | 单相 STATCOM | 0x0105 | Python / PySide6 | DSP 4号工程 / CPLD Modbus V0105 |
| [CPLD_BOARD_TEST_HOST_V0106](CPLD_BOARD_TEST_HOST_V0106/) | EPM1270 板卡测试 | 0x0106 | Python / Tkinter | CPLD_EPM1270_BOARD_TEST |

## 安全声明

所有配套当前阶段固件的上位机软件均处于测试状态，下位机功率输出处于硬封锁状态；板卡测试工程仅在受控 START 状态下进行小信号/互补波形测试。
