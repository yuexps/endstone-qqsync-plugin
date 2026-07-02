"""
硬件系统诊断信息收集模块
"""

import platform
import subprocess
import sys
from typing import Any
import psutil


def get_cpu_name() -> str:
    """获取处理器型号名称"""
    if sys.platform.startswith("win"):
        try:
            result = subprocess.run(
                ["powershell", "-Command", "Get-WmiObject -Class Win32_Processor | Select-Object -ExpandProperty Name"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception:
            pass

        try:
            result = subprocess.run(
                ["wmic", "cpu", "get", "name", "/format:value"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                for line in result.stdout.split("\n"):
                    if line.startswith("Name="):
                        cpu_name = line.split("=", 1)[1].strip()
                        if cpu_name:
                            return cpu_name
        except Exception:
            pass

        return platform.processor()

    if sys.platform.startswith("linux"):
        try:
            with open("/proc/cpuinfo", "r", encoding="utf-8") as f:
                for line in f:
                    if "model name" in line:
                        return line.split(":", 1)[1].strip()
        except Exception:
            pass

    return platform.processor()


def get_cpu_max_freq() -> float | None:
    """获取处理器额定最大主频（单位: GHz）"""
    if sys.platform.startswith("win"):
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
        except Exception:
            pass

        try:
            result = subprocess.run(
                ["wmic", "cpu", "get", "MaxClockSpeed", "/format:value"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                for line in result.stdout.split("\n"):
                    if line.startswith("MaxClockSpeed="):
                        freq_str = line.split("=", 1)[1].strip()
                        if freq_str and freq_str.isdigit():
                            return int(freq_str) / 1000
        except Exception:
            pass

    elif sys.platform.startswith("linux"):
        try:
            with open("/proc/cpuinfo", "r", encoding="utf-8") as f:
                for line in f:
                    if "cpu MHz" in line:
                        return float(line.split(":", 1)[1].strip()) / 1000

            try:
                with open("/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq", "r", encoding="utf-8") as f:
                    return int(f.read().strip()) / 1000000
            except FileNotFoundError:
                pass
        except Exception:
            pass

    cpu_freq = psutil.cpu_freq()
    if cpu_freq and cpu_freq.max:
        return cpu_freq.max / 1000
    return None


def get_os_info() -> str:
    """获取操作系统分发版本描述"""
    if sys.platform.startswith("linux"):
        try:
            with open("/etc/os-release", "r", encoding="utf-8") as f:
                os_info = {}
                for line in f:
                    if "=" in line:
                        key, value = line.strip().split("=", 1)
                        os_info[key] = value.strip('"')
                if "PRETTY_NAME" in os_info:
                    return os_info["PRETTY_NAME"]
                if "NAME" in os_info and "VERSION" in os_info:
                    return f"{os_info['NAME']} {os_info['VERSION']}"
        except FileNotFoundError:
            pass
        return f"Linux {platform.release()}"
    return f"{platform.system()} {platform.release()}"


def get_system_info_dict() -> dict[str, Any]:
    """生成结构化系统诊断数据集"""
    os_info = get_os_info()
    cpu_model = get_cpu_name()
    cpu_max_freq = get_cpu_max_freq()
    cpu_freq = psutil.cpu_freq()
    cpu_usage = max(0.0, psutil.cpu_percent(interval=None))  # 不使用阻塞等待
    mem = psutil.virtual_memory()

    disk_info = []
    try:
        disk_partitions = psutil.disk_partitions()
        processed_devices = set()

        for partition in disk_partitions:
            if partition.device in processed_devices:
                continue
            if partition.fstype in ["tmpfs", "devtmpfs", "sysfs", "proc", "cgroup", "cgroup2"]:
                continue
            try:
                partition_usage = psutil.disk_usage(partition.mountpoint)
                disk_info.append(
                    {
                        "device": partition.device,
                        "mountpoint": partition.mountpoint,
                        "fstype": partition.fstype,
                        "total_gb": round(partition_usage.total / (1024**3), 2),
                        "used_gb": round(partition_usage.used / (1024**3), 2),
                        "free_gb": round(partition_usage.free / (1024**3), 2),
                        "percent": round((partition_usage.used / partition_usage.total) * 100, 1),
                    }
                )
                processed_devices.add(partition.device)
            except PermissionError:
                disk_info.append({"device": partition.device, "error": "无法访问"})
            except Exception:
                pass
    except Exception:
        pass

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
