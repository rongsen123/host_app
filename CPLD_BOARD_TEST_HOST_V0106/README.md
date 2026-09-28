# CPLD 板卡测试上位机 (CPLD_BOARD_TEST_HOST_V0106)

配套 CPLD 工程：`CPLD_PRJ/CPLD_EPM1270_BOARD_TEST` (固件版本 `0x0106`)。  
通信接口：光纤串口（Modbus RTU 从站地址 1、波特率 115200、8N1）。

---

## 目录文件说明

- `board_test_gui.py`：基于 Tkinter 与 pyserial 的图形化监控与控制上位机。
- `board_test_cli.py`：命令行调试脚本，支持 Modbus 读写、启停控制与故障清除。
- `build_exe.bat`：使用 PyInstaller 独立打包生成免安装 Windows 可执行程序脚本。
- `requirements.txt`：Python 运行依赖环境清单。
- `VERSION`：当前配套版本号（0.1.6）。

---

## 运行环境与安装

建议使用 Python 3.8 及以上环境：

```bash
pip install -r requirements.txt
```

---

## 图形界面使用说明

运行 GUI：

```bash
python board_test_gui.py
```

1. **串口连接**：选择光纤串口适配器对应的 COM 端口，点击“连接”。界面将以 100 ms 周期自动轮询下位机状态。
2. **实时监控**：
   - 电压 ADC 原始码（0～4095）。
   - 温度脉冲计数 / 100 ms。
   - 开入状态（旁路反馈、对端故障、备用开入、硬件过压、驱动故障等）。
   - 原始故障字与已屏蔽故障字解码。
3. **参数配置（仅 STOP 状态可写）**：
   - 故障屏蔽寄存器（0x1100）：默认 0x0003（屏蔽旁路反馈与对端故障）。
   - 故障模拟寄存器（0x1101）：可注入模拟故障位。
   - 开出控制寄存器（0x1102）：bit0 风机、bit1 旁路、bit2 PWM（默认 0x0004 申请 PWM）。
   - PWM 周期（0x1103）与分界时钟数（0x1104）。
   - 软件过压 ADC 阈值（0x1000）。
4. **控制命令**：
   - **START**：下发启动命令（写入 0x0100=1）。
   - **STOP**：下发停机命令（写入 0x0100=0）。
   - **CLEAR**：清除已消失的锁存故障（写入 0x0101=0xA55A）。

---

## 命令行 CLI 使用说明

```bash
# 查询当前状态及开入寄存器
python board_test_cli.py COM5 status
python board_test_cli.py COM5 read 0x000E --input

# 配置开出控制并启动
python board_test_cli.py COM5 write 0x1102 0x0004
python board_test_cli.py COM5 start

# 停机与清除故障
python board_test_cli.py COM5 stop
python board_test_cli.py COM5 clear
```

---

## 独立 EXE 打包

在 Windows 环境下双击或执行：

```cmd
build_exe.bat
```

打包完成后将在 `dist/` 目录下生成 `SST_BoardTest_Console_v5.exe`。
