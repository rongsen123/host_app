# Changelog - host_app

All notable changes to the host applications repository will be documented in this file.

## [0.1.6] - 2026-09-28

### Added
- 收录 EPM1270 板级测试上位机 `CPLD_BOARD_TEST_HOST_V0106/`。
- 提供基于 Tkinter 的 GUI (`board_test_gui.py`) 与 CLI (`board_test_cli.py`) 工具。
- 支持 Modbus RTU (0x0106)、ADS7818 采样与温度频率监控、互补 PWM 周期/占空比调节、故障模拟与屏蔽配置。
- 增加 PyInstaller 打包构建脚本 `build_exe.bat` 及 Python 依赖清单 `requirements.txt`。

## [0.1.5] - 2026-08-19

### Added
- 初始化 `host_app` 独立上位机仓库。
- 收录 `STATCOM_HOST_V0105` 监控上位机工程，配套基线 `0x0105`。
- 增加仓库根 `PROJECT_INDEX.md` 与总文件清单。
