"""
硬件系统诊断信息收集模块
"""

import logging
import platform
import subprocess
import sys
from typing import Any
import psutil

logger = logging.getLogger(__name__)

# sysfs 主频文件以 kHz 记录频率，换算为 GHz 的除数
_KHZ_PER_GHZ = 1000000


def _parse_wmic_line(line: str, key: str) -> str | None:
    """解析单行 wmic 键值文本，取出指定键的非空值"""
    if not line.startswith(key):
        return None
    value = line.split("=", 1)[1].strip()
    return value or None


def _find_wmic_value(stdout: str, key: str) -> str | None:
    """从 wmic 输出文本中查找指定键的非空值"""
    for line in stdout.split("\n"):
        value = _parse_wmic_line(line, key)
        if value:
            return value
    return None


def _read_first_matching_line(path: str, marker: str) -> str | None:
    """读取文本文件中首个含标记行的冒号后内容"""
    with open(path, "r", encoding="utf-8") as f:
        matched_line = next((line for line in f if marker in line), None)
    if matched_line is None:
        return None
    return matched_line.split(":", 1)[1].strip()


def _query_cpu_name_by_powershell() -> str | None:
    """通过 PowerShell 查询处理器型号"""
    try:
        result = subprocess.run(
            ["powershell", "-Command", "Get-WmiObject -Class Win32_Processor | Select-Object -ExpandProperty Name"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception as e:
        logger.debug(f"PowerShell 读取 CPU 型号失败，改用 wmic: {e}")
    return None


def _query_cpu_name_by_wmic() -> str | None:
    """通过 wmic 查询处理器型号"""
    try:
        result = subprocess.run(
            ["wmic", "cpu", "get", "name", "/format:value"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return _find_wmic_value(result.stdout, "Name=")
    except Exception as e:
        logger.debug(f"wmic 读取 CPU 型号失败，改用 platform.processor: {e}")
    return None


def _read_cpu_name_on_windows() -> str:
    """读取 Windows 平台处理器型号"""
    cpu_name = _query_cpu_name_by_powershell()
    if cpu_name:
        return cpu_name
    cpu_name = _query_cpu_name_by_wmic()
    if cpu_name:
        return cpu_name
    return platform.processor()


def _read_cpu_name_on_linux() -> str | None:
    """读取 Linux 平台处理器型号"""
    try:
        return _read_first_matching_line("/proc/cpuinfo", "model name")
    except Exception as e:
        logger.debug(f"读取 /proc/cpuinfo 获取 CPU 型号失败: {e}")
    return None


def get_cpu_name() -> str:
    """获取处理器型号名称"""
    if sys.platform.startswith("win"):
        return _read_cpu_name_on_windows()
    if sys.platform.startswith("linux"):
        cpu_name = _read_cpu_name_on_linux()
        if cpu_name is not None:
            return cpu_name
    return platform.processor()


def _query_cpu_freq_by_powershell() -> float | None:
    """通过 PowerShell 查询处理器最大主频"""
    try:
        result = subprocess.run(
            [
                "powershell",
                "-Command",
                "Get-WmiObject -Class Win32_Processor | Select-Object -ExpandProperty MaxClockSpeed",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip()) / 1000
    except Exception as e:
        logger.debug(f"PowerShell 读取 CPU 主频失败，改用 wmic: {e}")
    return None


def _parse_wmic_freq_mhz(stdout: str) -> int | None:
    """从 wmic 输出文本中解析 MHz 整数主频"""
    freq_str = _find_wmic_value(stdout, "MaxClockSpeed=")
    if freq_str and freq_str.isdigit():
        return int(freq_str)
    return None


def _query_cpu_freq_by_wmic() -> float | None:
    """通过 wmic 查询处理器最大主频"""
    try:
        result = subprocess.run(
            ["wmic", "cpu", "get", "MaxClockSpeed", "/format:value"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        freq_mhz = _parse_wmic_freq_mhz(result.stdout)
        if freq_mhz is not None:
            return freq_mhz / 1000
    except Exception as e:
        logger.debug(f"wmic 读取 CPU 主频失败，改读 /proc/cpuinfo: {e}")
    return None


def _read_cpu_max_freq_on_windows() -> float | None:
    """读取 Windows 平台处理器最大主频"""
    freq = _query_cpu_freq_by_powershell()
    if freq is not None:
        return freq
    return _query_cpu_freq_by_wmic()


def _read_cpu_freq_from_sysfs() -> float | None:
    """从 sysfs 读取处理器最大频率并换算为 GHz"""
    try:
        with open("/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq", "r", encoding="utf-8") as f:
            return int(f.read().strip()) / _KHZ_PER_GHZ
    except FileNotFoundError as e:
        logger.debug(f"未找到 cpuinfo_max_freq，改由 psutil 读取主频: {e}")
    return None


def _read_cpu_max_freq_on_linux() -> float | None:
    """读取 Linux 平台处理器最大主频"""
    try:
        freq_mhz = _read_first_matching_line("/proc/cpuinfo", "cpu MHz")
        if freq_mhz is not None:
            return float(freq_mhz) / 1000
        return _read_cpu_freq_from_sysfs()
    except Exception as e:
        logger.debug(f"读取 /proc/cpuinfo 获取 CPU 主频失败: {e}")
    return None


def _read_platform_cpu_max_freq() -> float | None:
    """按运行平台读取处理器最大主频"""
    if sys.platform.startswith("win"):
        return _read_cpu_max_freq_on_windows()
    if sys.platform.startswith("linux"):
        return _read_cpu_max_freq_on_linux()
    return None


def get_cpu_max_freq() -> float | None:
    """获取处理器额定最大主频（单位: GHz）"""
    freq = _read_platform_cpu_max_freq()
    if freq is not None:
        return freq

    cpu_freq = psutil.cpu_freq()
    if cpu_freq and cpu_freq.max:
        return cpu_freq.max / 1000
    return None


def _parse_os_release_entry(line: str) -> tuple[str, str]:
    """解析 os-release 文件的单行键值对"""
    key, value = line.strip().split("=", 1)
    return key, value.strip('"')


def _read_os_release() -> dict[str, str]:
    """读取 /etc/os-release 键值对"""
    with open("/etc/os-release", "r", encoding="utf-8") as f:
        lines = f.readlines()
    entries = [_parse_os_release_entry(line) for line in lines if "=" in line]
    return dict(entries)


def _format_linux_os_release() -> str | None:
    """从 /etc/os-release 生成系统描述"""
    try:
        os_info = _read_os_release()
    except FileNotFoundError as e:
        logger.debug(f"未找到 /etc/os-release，改用 platform 信息: {e}")
        return None
    if "PRETTY_NAME" in os_info:
        return os_info["PRETTY_NAME"]
    if "NAME" in os_info and "VERSION" in os_info:
        return f"{os_info['NAME']} {os_info['VERSION']}"
    return None


def get_os_info() -> str:
    """获取操作系统分发版本描述"""
    if sys.platform.startswith("linux"):
        os_release = _format_linux_os_release()
        if os_release is not None:
            return os_release
        return f"Linux {platform.release()}"
    return f"{platform.system()} {platform.release()}"


def _collect_partition_info(partition: Any, processed_devices: set[str]) -> list[dict[str, Any]]:
    """收集单个磁盘分区的容量信息"""
    if partition.device in processed_devices:
        return []
    if partition.fstype in ["tmpfs", "devtmpfs", "sysfs", "proc", "cgroup", "cgroup2"]:
        return []
    try:
        partition_usage = psutil.disk_usage(partition.mountpoint)
        info = {
            "device": partition.device,
            "mountpoint": partition.mountpoint,
            "fstype": partition.fstype,
            "total_gb": round(partition_usage.total / (1024**3), 2),
            "used_gb": round(partition_usage.used / (1024**3), 2),
            "free_gb": round(partition_usage.free / (1024**3), 2),
            "percent": round((partition_usage.used / partition_usage.total) * 100, 1),
        }
    except PermissionError:
        return [{"device": partition.device, "error": "无法访问"}]
    except Exception as e:
        logger.debug(f"跳过无法统计的磁盘 {partition.device}: {e}")
        return []
    processed_devices.add(partition.device)
    return [info]


def _collect_disk_info() -> list[dict[str, Any]]:
    """收集所有磁盘分区的容量信息"""
    disk_info: list[dict[str, Any]] = []
    try:
        processed_devices: set[str] = set()

        for partition in psutil.disk_partitions():
            disk_info.extend(_collect_partition_info(partition, processed_devices))
    except Exception as e:
        logger.debug(f"读取磁盘分区信息失败: {e}")
    return disk_info


def get_system_info_dict() -> dict[str, Any]:
    """生成结构化系统诊断数据集"""
    os_info = get_os_info()
    cpu_model = get_cpu_name()
    cpu_max_freq = get_cpu_max_freq()
    cpu_freq = psutil.cpu_freq()
    cpu_usage = max(0.0, psutil.cpu_percent(interval=None))  # 不使用阻塞等待
    mem = psutil.virtual_memory()
    disk_info = _collect_disk_info()

    return {
        "os": os_info,
        "cpu": {
            "model": cpu_model,
            "max_freq_ghz": cpu_max_freq,
            "current_freq_ghz": (cpu_freq.current / 1000) if cpu_freq and cpu_freq.current else None,
            "usage_percent": cpu_usage,
            "physical_cores": psutil.cpu_count(logical=False) or 0,
            "logical_cores": psutil.cpu_count(logical=True) or 0,
        },
        "memory": {
            "total_gb": round(mem.total / (1024**3), 2),
            "used_gb": round(mem.used / (1024**3), 2),
            "percent": mem.percent,
        },
        "disks": disk_info,
    }
