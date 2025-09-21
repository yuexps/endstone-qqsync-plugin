"""
时间工具模块
提供网络时间获取和时间相关的实用函数
"""

import datetime
import socket
import struct
import time
from typing import Tuple

# 中国时区 (UTC+8)
CHINA_TZ = datetime.timezone(datetime.timedelta(hours=8))


class TimeUtils:
    """时间工具类"""
    
    # NTP服务器列表，按优先级排序（基于中国大陆测试结果优化）
    NTP_SERVERS = [
        'ntp1.aliyun.com',       # 阿里云NTP1 (45ms, 100%成功率) 
        'cn.ntp.org.cn',         # 中国NTP服务器 (53ms, 100%成功率) 
        'time.edu.cn',           # 中国教育网 (58ms, 100%成功率)
        'ntp.aliyun.com',        # 阿里云主服务器 (61ms, 100%成功率)
        'cn.pool.ntp.org',       # 中国NTP池 (62ms, 100%成功率)
        'ntp2.aliyun.com',       # 阿里云NTP2 (68ms, 100%成功率)
        'time.windows.com',      # 微软时间服务器 (120ms, 33%成功率) - 备用
        'time.nist.gov',         # 美国标准时间 (210ms, 100%成功率) - 备用
        'pool.ntp.org',          # 国际NTP池 (40ms, 33%成功率) - 不稳定
        'time.cloudflare.com',   # Cloudflare (310ms, 67%成功率) - 最后备用
    ]
    
    # 时间准确性状态
    _local_time_accurate = None  # None=未检查, True=准确, False=不准确
    _time_check_performed = False
    
    @classmethod
    def check_local_time_accuracy(cls, tolerance_seconds: int = 30) -> bool:
        """
        检查本地时间是否准确
        
        Args:
            tolerance_seconds: 允许的时间差阈值（秒）
            
        Returns:
            bool: 本地时间是否准确
        """
        try:
            # 获取网络时间 - 优先使用前五个中国大陆服务器
            network_time = None
            for server in cls.NTP_SERVERS[:5]:  # 使用前5个中国大陆服务器进行检查
                try:
                    network_time = cls._get_ntp_time(server, timeout=2)
                    if network_time:
                        break
                except Exception:
                    continue
            
            if network_time is None:
                # 无法获取网络时间，假设本地时间准确
                cls._local_time_accurate = True
                return True
            
            # 比较本地时间和网络时间
            local_time = datetime.datetime.now(CHINA_TZ)
            
            # 处理时区不匹配问题
            if network_time.tzinfo is None:
                network_time = network_time.replace(tzinfo=CHINA_TZ)
            
            time_diff = abs((local_time - network_time).total_seconds())
            
            # 如果时间差小于阈值，认为本地时间准确
            is_accurate = time_diff <= tolerance_seconds
            cls._local_time_accurate = is_accurate
            
            return is_accurate
            
        except Exception:
            # 检查过程出错，假设本地时间准确
            cls._local_time_accurate = True
            return True
    
    @classmethod
    def initialize_time_system(cls) -> dict:
        """
        初始化时间系统，检查本地时间准确性
        
        Returns:
            dict: 初始化结果信息
        """
        if cls._time_check_performed:
            return {
                'already_checked': True,
                'local_time_accurate': cls._local_time_accurate,
                'message': '时间系统已初始化'
            }
        
        try:  
            # 检查本地时间准确性
            is_accurate = cls.check_local_time_accuracy()
            cls._time_check_performed = True
            
            local_time = datetime.datetime.now(CHINA_TZ)
            
            if is_accurate:
                print(f"✅ 本地时间准确，当前时间(UTC+8): {local_time.strftime('%Y-%m-%d %H:%M:%S')}")
                return {
                    'success': True,
                    'local_time_accurate': True,
                    'current_time': local_time,
                    'message': '本地时间准确，优先使用本地时间'
                }
            else:
                print(f"⚠️ 本地时间不准确，将使用网络时间")
                network_time, _ = cls.get_network_time()
                print(f"📅 网络时间: {network_time.strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"📅 本地时间: {local_time.strftime('%Y-%m-%d %H:%M:%S')}")
                return {
                    'success': True,
                    'local_time_accurate': False,
                    'current_time': network_time,
                    'local_time': local_time,
                    'message': '本地时间不准确，使用网络时间'
                }
                
        except Exception as e:
            print(f"❌ 时间系统初始化失败: {e}")
            cls._local_time_accurate = True  # 默认假设本地时间准确
            cls._time_check_performed = True
            return {
                'success': False,
                'local_time_accurate': True,
                'error': str(e),
                'message': '时间检查失败，默认使用本地时间'
            }

    @classmethod
    def get_network_time(cls, timeout: int = 3) -> Tuple[datetime.datetime, bool]:
        """
        获取网络时间，失败时返回本地时间
        
        Args:
            timeout: 网络请求超时时间（秒）
            
        Returns:
            Tuple[datetime.datetime, bool]: (时间对象, 是否为网络时间)
        """
        try:
            # 尝试从NTP服务器获取时间
            for server in cls.NTP_SERVERS:
                try:
                    network_time = cls._get_ntp_time(server, timeout)
                    if network_time:
                        return network_time, True
                except Exception:
                    continue
            
            # 所有NTP服务器都失败，返回本地时间
            return datetime.datetime.now(CHINA_TZ), False
            
        except Exception:
            # 完全失败，返回本地时间
            return datetime.datetime.now(CHINA_TZ), False
    
    @classmethod
    def _get_ntp_time(cls, server: str, timeout: int = 3) -> datetime.datetime:
        """
        从指定NTP服务器获取时间
        
        Args:
            server: NTP服务器地址
            timeout: 超时时间
            
        Returns:
            datetime.datetime: 网络时间
            
        Raises:
            Exception: 网络请求失败
        """
        # 创建NTP请求包
        ntp_packet = b'\x1b' + 47 * b'\0'
        
        # 发送请求
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(timeout)
            sock.sendto(ntp_packet, (server, 123))
            
            # 接收响应
            data, _ = sock.recvfrom(1024)
            
            # 解析NTP时间戳
            timestamp = struct.unpack('!12I', data)[10]
            timestamp -= 2208988800  # NTP纪元转Unix纪元
            
            network_time = datetime.datetime.fromtimestamp(timestamp, CHINA_TZ)
            return network_time
            
        finally:
            sock.close()
    
    @classmethod
    def get_current_time_info(cls) -> dict:
        """
        获取当前时间信息
        
        Returns:
            dict: 包含时间信息的字典
        """
        current_time, is_network_time = cls.get_current_time()
        
        # 确定时间源描述
        if cls._local_time_accurate:
            source = '🕐 本地时间（已验证准确）'
        elif is_network_time:
            source = '🌐 网络时间（本地时间不准确）'
        else:
            source = '⚠️ 本地时间（网络不可用）'
        
        return {
            'time': current_time,
            'is_network_time': is_network_time,
            'is_local_time_accurate': cls._local_time_accurate,
            'formatted_time': current_time.strftime('%Y-%m-%d %H:%M:%S'),
            'source': source
        }
    
    @classmethod
    def calculate_uptime(cls, start_time: datetime.datetime) -> dict:
        """
        计算运行时间
        
        Args:
            start_time: 启动时间
            
        Returns:
            dict: 包含运行时间信息的字典
        """
        current_time, is_network_time = cls.get_current_time()
        
        # 处理时区不匹配问题
        if start_time.tzinfo is None and current_time.tzinfo is not None:
            start_time = start_time.replace(tzinfo=CHINA_TZ)
        elif start_time.tzinfo is not None and current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=CHINA_TZ)
        
        uptime = current_time - start_time
        
        # 格式化运行时间
        days = uptime.days
        hours, remainder = divmod(uptime.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        # 生成友好的时间字符串
        if days > 0:
            uptime_str = f"{days}天 {hours}小时 {minutes}分钟"
        elif hours > 0:
            uptime_str = f"{hours}小时 {minutes}分钟"
        else:
            uptime_str = f"{minutes}分钟 {seconds}秒"
        
        return {
            'uptime': uptime,
            'uptime_str': uptime_str,
            'current_time': current_time,
            'is_time_accurate': cls._local_time_accurate if not is_network_time else True,
            'is_network_time': is_network_time,
            'days': days,
            'hours': hours,
            'minutes': minutes,
            'seconds': seconds
        }
    
    @classmethod
    def format_datetime(cls, dt: datetime.datetime, format_str: str = '%Y-%m-%d %H:%M:%S') -> str:
        """
        格式化日期时间
        
        Args:
            dt: 日期时间对象
            format_str: 格式字符串
            
        Returns:
            str: 格式化后的时间字符串
        """
        return dt.strftime(format_str)
    
    @classmethod
    def get_current_time(cls) -> Tuple[datetime.datetime, bool]:
        """
        智能获取当前时间（优先本地时间，必要时使用网络时间）
        
        Returns:
            Tuple[datetime.datetime, bool]: (时间对象, 是否为网络时间)
        """
        # 如果还没有检查过时间准确性，先进行检查
        if not cls._time_check_performed:
            cls.initialize_time_system()
        
        # 如果本地时间准确，优先使用本地时间
        if cls._local_time_accurate:
            return datetime.datetime.now(CHINA_TZ), False
        else:
            # 本地时间不准确，使用网络时间
            return cls.get_network_time()

    @classmethod
    def get_timestamp(cls) -> float:
        """
        获取当前时间戳（智能选择本地时间或网络时间）
        
        Returns:
            float: 时间戳
        """
        # 如果还没有检查过时间准确性，先进行检查
        if not cls._time_check_performed:
            cls.initialize_time_system()
        
        # 如果本地时间准确，优先使用本地时间
        if cls._local_time_accurate:
            return time.time()
        else:
            # 本地时间不准确，使用网络时间
            current_time, _ = cls.get_network_time()
            return current_time.timestamp()
    
    @classmethod
    def time_difference_str(cls, start_time: datetime.datetime, end_time: datetime.datetime = None) -> str:
        """
        计算时间差并返回友好字符串
        
        Args:
            start_time: 开始时间
            end_time: 结束时间，默认为当前时间
            
        Returns:
            str: 时间差的友好字符串
        """
        if end_time is None:
            end_time, _ = cls.get_current_time()
        
        # 处理时区不匹配问题
        if start_time.tzinfo is None and end_time.tzinfo is not None:
            start_time = start_time.replace(tzinfo=CHINA_TZ)
        elif start_time.tzinfo is not None and end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=CHINA_TZ)
        
        time_diff = end_time - start_time
        days = time_diff.days
        hours, remainder = divmod(time_diff.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        if days > 0:
            return f"{days}天 {hours}小时 {minutes}分钟"
        elif hours > 0:
            return f"{hours}小时 {minutes}分钟"
        elif minutes > 0:
            return f"{minutes}分钟 {seconds}秒"
        else:
            return f"{seconds}秒"
