"""
时区一致的时间与运行时长工具
"""

import datetime
import time
from typing import Any


CHINA_TZ = datetime.timezone(datetime.timedelta(hours=8))

# 每小时秒数，用于运行时长换算
_SECONDS_PER_HOUR = 3600


class Timing:
    """东八区时间计算与格式化"""

    @classmethod
    def get_current_time(cls) -> datetime.datetime:
        """获取带东八区时区的当前时间"""
        return datetime.datetime.now(CHINA_TZ)

    @staticmethod
    def get_timestamp() -> int:
        """获取当前整型 UNIX 时间戳"""
        return int(time.time())

    @staticmethod
    def format_datetime(dt: datetime.datetime, format_str: str = "%Y-%m-%d %H:%M:%S") -> str:
        """格式化日期时间"""
        return dt.strftime(format_str)

    @classmethod
    def get_current_time_info(cls) -> dict[str, Any]:
        """获取服务器当前时间的详细结构信息"""
        current_time = cls.get_current_time()
        return {
            "time": current_time,
            "is_network_time": False,
            "is_local_time_accurate": True,
            "formatted_time": cls.format_datetime(current_time),
            "source": "服务器时间",
        }

    @classmethod
    def calculate_uptime(cls, start_time: datetime.datetime) -> dict[str, Any]:
        """计算自服务启动以来的持续运行时间"""
        current_time = cls.get_current_time()

        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=CHINA_TZ)

        uptime = current_time - start_time
        days = uptime.days
        hours, remainder = divmod(uptime.seconds, _SECONDS_PER_HOUR)
        minutes, seconds = divmod(remainder, 60)

        uptime_str = cls._format_duration(days, hours, minutes, seconds)

        return {
            "uptime": uptime,
            "uptime_str": uptime_str,
            "current_time": current_time,
            "days": days,
            "hours": hours,
            "minutes": minutes,
            "seconds": seconds,
        }

    @staticmethod
    def _format_duration(days: int, hours: int, minutes: int, seconds: int) -> str:
        """格式化时长数值为易读文本"""
        if days > 0:
            return f"{days}天 {hours}小时 {minutes}分钟"
        if hours > 0:
            return f"{hours}小时 {minutes}分钟"
        if minutes > 0:
            return f"{minutes}分钟 {seconds}秒"
        return f"{seconds}秒"
