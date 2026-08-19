"""自定义控件：状态指示灯、数值卡片。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class LedIndicator(QWidget):
    """圆形状态指示灯。"""

    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._color = QColor("#3a4a5c")
        self._text = text
        self._label = QLabel(text, self)
        self._label.setStyleSheet(
            "color:#8fa1b3;font-size:13px;font-weight:500;background:transparent;"
        )
        self._label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self.setFixedHeight(26)
        self._label.setFixedHeight(26)

    def set_on(self, on: bool, color: str | None = None) -> None:
        if color:
            self._color = QColor(color)
        elif on:
            self._color = QColor("#25c06d")
        else:
            self._color = QColor("#3a4a5c")
        self.update()

    def set_text(self, text: str) -> None:
        self._text = text
        self._label.setText(text)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self._color)
        r = 7
        p.drawEllipse(6, self.height() // 2 - r, r * 2, r * 2)
        p.end()

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._label.setGeometry(24, 0, self.width() - 24, self.height())
        super().resizeEvent(event)


class StatCard(QWidget):
    """数值卡片：标题 + 数值 + 单位 + 附加/次要说明。"""

    def __init__(self, title: str, unit: str = "", secondary: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumHeight(105)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(4)

        self._title = QLabel(title, self)
        self._title.setObjectName("Caption")
        self._title.setStyleSheet("color:#94a3b8;font-size:12px;font-weight:600;")
        layout.addWidget(self._title)

        self._value = QLabel("--", self)
        self._value.setObjectName("ValueLabel")
        self._value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._value.setStyleSheet(
            "color:#f8fafc;font-size:24px;font-weight:700;"
            "font-family:'Consolas','Segoe UI',monospace;"
            "background-color:#0b1017;border:1px solid #223040;"
            "border-radius:6px;padding:4px 10px;min-height:36px;"
        )
        layout.addWidget(self._value)

        self._unit = QLabel(unit, self)
        self._unit.setObjectName("UnitLabel")
        self._unit.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._unit.setStyleSheet("color:#64748b;font-size:11px;font-weight:500;")
        layout.addWidget(self._unit)

        self._secondary = QLabel(secondary, self)
        self._secondary.setStyleSheet(
            "color:#8fa1b3;font-size:11px;font-family:'Consolas','Microsoft YaHei UI',monospace;background:transparent;"
        )
        self._secondary.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._secondary.setWordWrap(True)
        layout.addWidget(self._secondary)

    def set_value(self, text: str) -> None:
        self._value.setText(text)

    def set_unit(self, text: str) -> None:
        self._unit.setText(text)

    def set_secondary(self, text: str) -> None:
        self._secondary.setText(text)

    def set_color(self, color: str) -> None:
        self._value.setStyleSheet(
            f"color:{color};font-size:24px;font-weight:700;"
            "font-family:'Consolas','Segoe UI',monospace;"
            "background-color:#0b1017;border:1px solid #223040;"
            "border-radius:6px;padding:4px 10px;min-height:36px;"
        )


def state_color(state_text: str) -> str:
    """根据状态文本返回语义颜色。"""
    colors = {
        "FAULT": "#e5484d",
        "RUN": "#25c06d",
        "SAFE_BOOT": "#8fa1b3",
        "ADC_VERIFY": "#f5b83d",
        "LINK_VERIFY": "#f5b83d",
        "PLL_MONITOR": "#f5b83d",
        "CONTROL_SHADOW": "#33b9c9",
        "CURRENT_LOOP": "#33b9c9",
        "VOLTAGE_LOOP": "#33b9c9",
    }
    return colors.get(state_text, "#2f6fed")


def format_uptime(ms: int) -> str:
    """毫秒 -> 时:分:秒.毫秒。"""
    total_s = ms / 1000.0
    h = int(total_s // 3600)
    m = int((total_s % 3600) // 60)
    s = total_s % 60
    return f"{h:02d}:{m:02d}:{s:04.1f}"