"""
theme.py - 图表主题和配色定义

包含暗色/亮色主题配色方案和 matplotlib 中文字体自动配置。
"""

import logging

try:
    import matplotlib
    matplotlib.use('Agg')  # 无头模式，不需要 GUI
    import matplotlib.pyplot as plt
    import matplotlib.font_manager as fm
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False

logger = logging.getLogger(__name__)

# 暗色主题配色
DARK_COLORS = {
    "bg": "#0d1117",
    "surface": "#161b22",
    "text": "#e6edf3",
    "text_dim": "#8b949e",
    "grid": "#21262d",
    "accent1": "#58a6ff",  # 蓝
    "accent2": "#3fb950",  # 绿
    "accent3": "#d29922",  # 橙
    "accent4": "#f85149",  # 红
    "accent5": "#bc8cff",  # 紫
    "accent6": "#39d2c0",  # 青
    "gradient": ["#0969da", "#1f6feb", "#388bfd", "#58a6ff", "#79c0ff"],
}

LIGHT_COLORS = {
    "bg": "#ffffff",
    "surface": "#f6f8fa",
    "text": "#1f2328",
    "text_dim": "#656d76",
    "grid": "#d0d7de",
    "accent1": "#0969da",
    "accent2": "#1a7f37",
    "accent3": "#9a6700",
    "accent4": "#cf222e",
    "accent5": "#8250df",
    "accent6": "#0891b2",
    "gradient": ["#0550ae", "#0969da", "#218bff", "#54aeff", "#80ccff"],
}


def configure_chinese_font():
    """配置 matplotlib 中文字体，解决图表乱码问题。"""
    if not HAS_MATPLOTLIB:
        return

    # 按优先级尝试中文字体
    candidate_fonts = [
        'Microsoft YaHei',  # Windows 雅黑
        'SimHei',           # 黑体
        'Noto Sans SC',     # Google Noto
        'DengXian',         # 等线
        'STHeiti',          # macOS 黑体
        'WenQuanYi Micro Hei',  # Linux
    ]

    available_fonts = {f.name for f in fm.fontManager.ttflist}
    chosen_font = None
    for font_name in candidate_fonts:
        if font_name in available_fonts:
            chosen_font = font_name
            break

    if chosen_font:
        plt.rcParams['font.sans-serif'] = [chosen_font, 'DejaVu Sans', 'Arial']
        plt.rcParams['font.family'] = 'sans-serif'
    else:
        print("[WARN] 未找到可用的中文字体，图表中文可能显示为方块")

    # 修复负号显示
    plt.rcParams['axes.unicode_minus'] = False
