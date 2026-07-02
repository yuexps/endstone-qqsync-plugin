"""
通用辅助函数模块
"""

import datetime
from .timing import Timing, CHINA_TZ


def format_timestamp(timestamp: int | float | None) -> str:
    """格式化秒级时间戳为规范的年月日时分秒"""
    if not timestamp:
        return "未知时间"
    try:
        dt = datetime.datetime.fromtimestamp(float(timestamp), CHINA_TZ)
        return Timing.format_datetime(dt)
    except Exception:
        return "未知时间"


def format_playtime(seconds: int) -> str:
    """将秒数格式化为时分秒文本"""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    remaining_seconds = seconds % 60

    if hours > 0:
        return f"{hours}小时{minutes}分钟"
    if minutes > 0:
        return f"{minutes}分钟"
    return f"{remaining_seconds}秒" if remaining_seconds > 0 else "少于1分钟"


def is_valid_qq_number(qq: str | None) -> bool:
    """校验QQ号是否符合5-11位纯数字的规范"""
    return bool(qq and qq.isdigit() and 5 <= len(qq) <= 11)


def clean_player_name(name: str | None) -> str:
    """清理并去除玩家游戏ID前后的空白字符"""
    return name.strip() if name else ""
