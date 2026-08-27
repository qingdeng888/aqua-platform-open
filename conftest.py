"""pytest 配置 - 添加 import 路径"""
import sys
from pathlib import Path

# 添加平台和网关的 app 目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent / "platform" / "app"))
sys.path.insert(0, str(Path(__file__).parent / "gateway" / "app"))
