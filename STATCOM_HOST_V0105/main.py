"""STATCOM 上位机启动入口。

用法：
    python main.py
"""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow


def main() -> int:
    # 高分屏支持
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("STATCOM 上位机监控系统")
    app.setFont(QFont("Microsoft YaHei UI", 10))

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())