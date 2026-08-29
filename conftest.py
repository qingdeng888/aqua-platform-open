# pytest 配置
#
# 仓库只有一个 "app" 包（gateway/app），这里统一把 gateway/ 插入 sys.path，
# 测试文件可直接 `from app.xxx import ...`（此前两个同名 app 包互相遮蔽的
# 按文件切换 sys.path 的做法已随 platform 模块下线一并移除）。
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "gateway"))
