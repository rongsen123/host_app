"""界面主题与配色（专业电力电子深色工业风格）。

彻底消除任何默认白色底色，实现一体化 Deep Slate / Dark UI。
"""

# 基础色系
BG_BASE = "#0a0e14"        # 全局最底背景 (深黑蓝)
BG_CONTAINER = "#121820"   # 内容容器/页面背景
BG_CARD = "#17202c"        # 面板卡片背景
BG_INPUT = "#1e2938"       # 输入框/次级卡片/未选中 Tab
BG_HOVER = "#263548"       # 悬浮背景
BORDER_COLOR = "#2a3b50"   # 边框线条色
BORDER_FOCUS = "#3b82f6"   # 聚焦/高亮边框色

# 文字与图标
TEXT_MAIN = "#f1f5f9"      # 主文字 (纯净高亮白)
TEXT_MUTED = "#94a3b8"     # 次要文字 (灰蓝)
TEXT_DISABLED = "#475569"  # 禁用文字

# 语义强调色
COLOR_PRIMARY = "#3b82f6"  # 科技蓝 (连接/主按钮)
COLOR_PRIMARY_HOVER = "#60a5fa"
COLOR_SUCCESS = "#10b981"  # 正常/在线/启动 (翡翠绿)
COLOR_SUCCESS_HOVER = "#34d399"
COLOR_DANGER = "#ef4444"   # 故障/停止 (宝石红)
COLOR_DANGER_HOVER = "#f87171"
COLOR_WARNING = "#f59e0b"  # 警示/未标定 (琥珀金)
COLOR_PURPLE = "#8b5cf6"   # PLL/特殊指标 (紫罗兰)
COLOR_CYAN = "#06b6d4"     # 青色

FONT_FAMILY = "'Microsoft YaHei UI', 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif"


def build_stylesheet() -> str:
    """返回全局无死角 Dark Theme QSS。"""
    return f"""
    /* 全局重置 */
    * {{
        font-family: {FONT_FAMILY};
        font-size: 13px;
        color: {TEXT_MAIN};
        selection-background-color: {COLOR_PRIMARY};
        selection-color: #ffffff;
    }}
    
    /* 顶层与常规窗口背景 */
    QMainWindow, QWidget, QDialog {{
        background-color: {BG_BASE};
    }}
    
    QWidget#Root {{
        background-color: {BG_BASE};
    }}

    /* 标签页 QTabWidget */
    QTabWidget {{
        background-color: {BG_BASE};
    }}
    QTabWidget::pane {{
        border: 1px solid {BORDER_COLOR};
        border-radius: 8px;
        background-color: {BG_CONTAINER};
        top: -1px;
        padding: 4px;
    }}
    QTabBar::tab {{
        background-color: {BG_BASE};
        color: {TEXT_MUTED};
        border: 1px solid {BORDER_COLOR};
        border-bottom: none;
        border-top-left-radius: 6px;
        border-top-right-radius: 6px;
        padding: 8px 18px;
        margin-right: 4px;
        font-weight: 600;
        font-size: 13px;
    }}
    QTabBar::tab:hover {{
        background-color: {BG_INPUT};
        color: {TEXT_MAIN};
        border-color: #3d516b;
    }}
    QTabBar::tab:selected {{
        background-color: {BG_CONTAINER};
        color: {COLOR_PRIMARY_HOVER};
        border-color: {BORDER_COLOR};
        border-bottom: 2px solid {COLOR_PRIMARY};
    }}

    /* 面板与卡片 */
    QFrame#Panel {{
        background-color: {BG_CARD};
        border: 1px solid {BORDER_COLOR};
        border-radius: 8px;
    }}
    QLabel#PanelTitle {{
        color: {TEXT_MAIN};
        font-size: 14px;
        font-weight: 700;
        letter-spacing: 0.5px;
    }}
    
    /* 数值与文本标签 */
    QLabel {{
        background-color: transparent;
    }}
    QLabel#ValueLabel {{
        color: #ffffff;
        font-size: 24px;
        font-weight: 700;
        font-family: 'Consolas', 'Microsoft YaHei UI', monospace;
        background-color: {BG_BASE};
        border: 1px solid {BORDER_COLOR};
        border-radius: 6px;
        padding: 6px 12px;
    }}
    QLabel#UnitLabel {{
        color: {TEXT_MUTED};
        font-size: 12px;
        font-weight: 500;
    }}
    QLabel#Caption {{
        color: {TEXT_MUTED};
        font-size: 12px;
    }}
    QLabel#StateValue {{
        color: {COLOR_PRIMARY_HOVER};
        font-size: 22px;
        font-weight: 700;
    }}

    /* 按钮通用 */
    QPushButton {{
        background-color: {BG_INPUT};
        color: {TEXT_MAIN};
        border: 1px solid {BORDER_COLOR};
        border-radius: 6px;
        padding: 6px 16px;
        font-weight: 600;
    }}
    QPushButton:hover {{
        background-color: {BG_HOVER};
        border-color: #3b506c;
        color: #ffffff;
    }}
    QPushButton:pressed {{
        background-color: #172433;
    }}
    QPushButton:disabled {{
        background-color: #111720;
        color: {TEXT_DISABLED};
        border-color: #1a2430;
    }}

    /* 主色/动作按钮 */
    QPushButton#PrimaryBtn {{
        background-color: {COLOR_PRIMARY};
        border: 1px solid {COLOR_PRIMARY};
        color: #ffffff;
    }}
    QPushButton#PrimaryBtn:hover {{
        background-color: {COLOR_PRIMARY_HOVER};
        border-color: {COLOR_PRIMARY_HOVER};
    }}
    QPushButton#StartBtn {{
        background-color: #065f46;
        border: 1px solid {COLOR_SUCCESS};
        color: #ecfdf5;
    }}
    QPushButton#StartBtn:hover {{
        background-color: {COLOR_SUCCESS};
        color: #ffffff;
    }}
    QPushButton#StopBtn {{
        background-color: #7f1d1d;
        border: 1px solid {COLOR_DANGER};
        color: #fef2f2;
    }}
    QPushButton#StopBtn:hover {{
        background-color: {COLOR_DANGER};
        color: #ffffff;
    }}

    /* 下拉选择框与输入控件 */
    QComboBox, QSpinBox, QLineEdit {{
        background-color: {BG_INPUT};
        color: {TEXT_MAIN};
        border: 1px solid {BORDER_COLOR};
        border-radius: 6px;
        padding: 5px 10px;
        min-height: 22px;
    }}
    QComboBox:hover, QSpinBox:hover, QLineEdit:hover {{
        border-color: #4a6382;
    }}
    QComboBox:focus, QSpinBox:focus, QLineEdit:focus {{
        border-color: {BORDER_FOCUS};
    }}
    QComboBox::drop-down {{
        border: none;
        width: 20px;
    }}
    QComboBox QAbstractItemView {{
        background-color: {BG_CARD};
        color: {TEXT_MAIN};
        border: 1px solid {BORDER_COLOR};
        selection-background-color: {COLOR_PRIMARY};
        selection-color: #ffffff;
        outline: 0;
        padding: 4px;
    }}

    /* 复选框 */
    QCheckBox {{
        spacing: 8px;
        background-color: transparent;
    }}
    QCheckBox::indicator {{
        width: 16px;
        height: 16px;
        border-radius: 4px;
        border: 1px solid {BORDER_COLOR};
        background-color: {BG_INPUT};
    }}
    QCheckBox::indicator:hover {{
        border-color: {COLOR_PRIMARY};
    }}
    QCheckBox::indicator:checked {{
        background-color: {COLOR_PRIMARY};
        border-color: {COLOR_PRIMARY};
    }}

    /* 日志与文本区 */
    QPlainTextEdit, QTextEdit {{
        background-color: #070a0f;
        border: 1px solid {BORDER_COLOR};
        border-radius: 6px;
        padding: 8px;
        font-family: Consolas, "Cascadia Code", "Courier New", monospace;
        font-size: 12px;
        color: #cbd5e1;
    }}

    /* 表格组件 */
    QTableWidget, QTableView {{
        background-color: #0d141e;
        alternate-background-color: #141d2b;
        border: 1px solid #223040;
        border-radius: 6px;
        gridline-color: #1a2533;
        color: #f1f5f9;
        outline: none;
    }}
    QTableCornerButton::section {{
        background-color: #090d13;
        border: none;
    }}
    QHeaderView::section {{
        background-color: #090d13;
        color: #94a3b8;
        border: none;
        border-bottom: 1px solid #223040;
        border-right: 1px solid #1a2533;
        padding: 6px;
        font-weight: 600;
    }}
    QTableWidget::item {{
        padding: 4px 8px;
        border-bottom: 1px solid #16202e;
        color: #f1f5f9;
    }}
    QTableWidget::item:selected {{
        background-color: #1e3a5f;
        color: #ffffff;
    }}

    /* 滚动条美化 */
    QScrollBar:vertical {{
        background: {BG_BASE};
        width: 10px;
        border-radius: 5px;
        margin: 0px;
    }}
    QScrollBar::handle:vertical {{
        background: #2a3a4e;
        border-radius: 5px;
        min-height: 25px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: #3e5470;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: none;
        height: 0px;
    }}
    QScrollBar:horizontal {{
        background: {BG_BASE};
        height: 10px;
        border-radius: 5px;
    }}
    QScrollBar::handle:horizontal {{
        background: #2a3a4e;
        border-radius: 5px;
        min-width: 25px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: #3e5470;
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        background: none;
        width: 0px;
    }}

    /* 浮窗提示 ToolTip */
    QToolTip {{
        background-color: {BG_CARD};
        color: {TEXT_MAIN};
        border: 1px solid {BORDER_COLOR};
        border-radius: 4px;
        padding: 6px 10px;
    }}
    """