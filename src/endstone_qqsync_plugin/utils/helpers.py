"""
工具函数模块
"""

import time
import datetime
from typing import Any


def format_timestamp(timestamp: int) -> str:
    """格式化时间戳"""
    try:
        dt = datetime.datetime.fromtimestamp(timestamp)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "未知时间"


def format_playtime(seconds: int) -> str:
    """格式化游戏时长"""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    
    if hours > 0:
        return f"{hours}小时{minutes}分钟"
    else:
        return f"{minutes}分钟"


def is_valid_qq_number(qq: str) -> bool:
    """检查QQ号是否有效"""
    return qq and qq.isdigit() and 5 <= len(qq) <= 11


def clean_player_name(name: str) -> str:
    """清理玩家名称"""
    return name.strip() if name else ""


def safe_get_config(config_manager, key: str, default: Any = None) -> Any:
    """安全获取配置项"""
    try:
        return config_manager.get_config(key, default)
    except Exception:
        return default
