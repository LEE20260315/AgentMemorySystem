"""tools 包：体积控制 / 清理工具。

v2.1.2 起作为正式包（含 __init__.py），供同步引擎静态导入
（from tools.shrink_memory_files import ...），这样 PyInstaller
能在打包时静态收集本包，修复"打包后 shrink_file 导入失败、体积控制
整体失效"的问题。
"""
