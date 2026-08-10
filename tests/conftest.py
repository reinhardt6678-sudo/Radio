"""
conftest.py - pytest 共享配置

确保 src 包可以被正确导入。
"""

import sys
from pathlib import Path

# 将项目根目录加入 sys.path
project_root = str(Path(__file__).parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)
