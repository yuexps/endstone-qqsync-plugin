"""
导入工具模块
统一处理第三方库的导入路径设置
"""

import os
import sys
from pathlib import Path

def setup_lib_path():
    """设置lib目录到Python路径"""
    # 获取插件根目录
    plugin_root = Path(__file__).parent.parent
    lib_path = plugin_root / 'lib'
    
    # 添加到Python路径（如果还没有添加的话）
    lib_path_str = str(lib_path)
    if lib_path_str not in sys.path:
        sys.path.insert(0, lib_path_str)

def import_websockets():
    """安全导入websockets库"""
    setup_lib_path()
    try:
        import websockets
        return websockets
    except ImportError as e:
        raise ImportError(f"无法导入websockets库: {e}")

# 在模块导入时自动设置路径
setup_lib_path()
