"""测试辅助：同名 "app" 包（gateway/app 与 platform/app）的 sys.path 切换。

conftest.py 故意不统一插入 app 路径（两个包同名会互相遮蔽），
各测试文件在导入目标包前调用本模块的 _switch_app()。
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent


def _switch_app(target: str):
    """切换 sys.path[0] 到 <target>/（"app" 包的父目录），并清理缓存的 app 模块。

    gateway/app 与 platform/app 是同名 "app" 包，切换前必须先移除
    sys.modules 中缓存的 app / app.* 模块，否则后续 import 仍解析到旧包。
    注意：插入的是 gateway/ 或 platform/ 本身（父目录），这样 `import app`
    才能解析到其下的 app/ 包目录。
    """
    for key in [k for k in list(sys.modules) if k == "app" or k.startswith("app.")]:
        del sys.modules[key]
    sys.path.insert(0, str(_REPO / target))
