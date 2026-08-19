# 上位机工程索引 (PROJECT_INDEX)

| 文件夹 | 适配设备 | 协议版本 | 配套 DSP 工程 | 配套 CPLD 工程 | 运行环境 | 关键状态 / 安全边界 |
| --- | --- | --- | --- | --- | --- | --- |
| [STATCOM_HOST_V0105](STATCOM_HOST_V0105/) | 单相 STATCOM | `0x0105` | `4_STATCOM_CURRENT_LOOP` | `CPLD_EPM1270_STATCOM_MODBUS` | Python 3.10+ / PySide6 | 原始码协议换算、保护阈值配置；功率输出硬封锁 |
