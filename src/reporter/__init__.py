"""
reporter 包 - 分析报告生成器

将 Reporter 类从子模块重新导出，保持对外接口不变。
"""

from .reporter import Reporter

__all__ = ["Reporter"]
