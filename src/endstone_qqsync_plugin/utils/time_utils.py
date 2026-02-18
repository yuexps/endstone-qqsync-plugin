"""
时间工具模块
提供统一的时间获取和格式化函数
"""

import datetime
import time
from typing import Tuple

# 中国时区 (UTC+8)
CHINA_TZ = datetime.timezone(datetime.timedelta(hours=8))


class TimeUtils:
    """时间工具类"""
    
    @classmethod
    def get_current_time(cls) -> Tuple[datetime.datetime, bool]:
        """获取当前时间（带时区）"""
        return datetime.datetime.now(CHINA_TZ), False

    @staticmethod
    def get_timestamp() -> float:
        """获取当前时间戳"""
        return time.time()
            
    @staticmethod
    def format_datetime(dt: datetime.datetime, format_str: str = '%Y-%m-%d %H:%M:%S') -> str:
        """格式化日期时间"""
        return dt.strftime(format_str)

    @classmethod
    def get_current_time_info(cls) -> dict:
        """获取当前时间信息"""
        current_time, _ = cls.get_current_time()
        
        return {
            'time': current_time,
            'is_network_time': False,
            'is_local_time_accurate': True,
            'formatted_time': cls.format_datetime(current_time),
            'source': '服务器时间'
        }
    
    @classmethod
    def calculate_uptime(cls, start_time: datetime.datetime) -> dict:
        """计算运行时间"""
        current_time, _ = cls.get_current_time()
        
        # 统一时区
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=CHINA_TZ)
        
        uptime = current_time - start_time
        
        # 计算时分秒
        days = uptime.days
        hours, remainder = divmod(uptime.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        # 生成显示字符串
        uptime_str = cls._format_duration(days, hours, minutes, seconds)
        
        return {
            'uptime': uptime,
            'uptime_str': uptime_str,
            'current_time': current_time,
            'days': days,
            'hours': hours,
            'minutes': minutes,
            'seconds': seconds
        }

    @staticmethod
    def _format_duration(days, hours, minutes, seconds) -> str:
        """内部方法：格式化持续时间"""
        if days > 0:
            return f"{days}天 {hours}小时 {minutes}分钟"
        elif hours > 0:
            return f"{hours}小时 {minutes}分钟"
        elif minutes > 0:
            return f"{minutes}分钟 {seconds}秒"
        else:
            return f"{seconds}秒"
