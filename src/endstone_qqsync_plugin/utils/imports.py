"""
第三方库导入安全加载器
"""

import sys
from pathlib import Path
from typing import Any


def setup_lib_path() -> None:
    """将插件内置的 lib 目录加入系统搜索路径"""
    plugin_root = Path(__file__).parent.parent
    lib_path = plugin_root / "lib"
    lib_path_str = str(lib_path)
    if lib_path_str not in sys.path:
        sys.path.insert(0, lib_path_str)


def import_websockets() -> Any:
    """导入内置或系统的 websockets 模块"""
    setup_lib_path()
    try:
        import websockets
        return websockets
    except ImportError as e:
        raise ImportError(f"无法导入 websockets 库: {e}")


# 模块加载时初始化运行环境
setup_lib_path()
