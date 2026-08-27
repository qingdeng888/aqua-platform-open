# pytest 配置说明
#
# 故意不在此处向 sys.path 插入任何 app 目录：
# gateway/app 与 platform/app 是两个同名 "app" 包，若同时插入会互相遮蔽，
# 导致后插入的一方永远无法被导入（以及跨包子模块解析错误）。
#
# 由各测试文件自行管理 sys.path：在导入目标包前调用文件内定义的
# _switch_app() 辅助函数，负责清理缓存的 app 模块并将目标 app 目录
# 插到 sys.path[0]。
