"""
exceptions.py - 自定义异常类型体系

提供结构化的错误类型，允许上层代码根据异常类别做差异化处理。
"""


class MilRadioError(Exception):
    """MilRadio 基础异常。"""
    pass


class ConfigError(MilRadioError):
    """配置错误（文件缺失、格式错误等）。"""
    pass


class ConnectionError(MilRadioError):
    """KiwiSDR 连接失败。"""
    pass


class HandshakeRejected(ConnectionError):
    """KiwiSDR 握手被拒绝（服务器满员、已关闭、IP 限制等）。"""
    pass


class NodeUnavailable(ConnectionError):
    """没有可用的 KiwiSDR 节点。"""
    pass


class AnalysisError(MilRadioError):
    """信号分析失败。"""
    pass
