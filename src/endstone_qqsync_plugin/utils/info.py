import sys
import platform
import psutil
import subprocess

def get_cpu_name():
    if sys.platform.startswith("win"):
        try:
            # 优先使用 PowerShell 获取 CPU 名称
            result = subprocess.run(
                ["powershell", "-Command", "Get-WmiObject -Class Win32_Processor | Select-Object -ExpandProperty Name"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
            pass
        
        try:
            # 回退到 WMIC 命令
            result = subprocess.run(
                ["wmic", "cpu", "get", "name", "/format:value"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if line.startswith('Name='):
                        cpu_name = line.split('=', 1)[1].strip()
                        if cpu_name:
                            return cpu_name
        except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
            pass
        
        # 最后回退到 platform.processor()
        return platform.processor()
        
    elif sys.platform.startswith("linux"):
        try:
            with open("/proc/cpuinfo", "r") as f:
                for line in f:
                    if "model name" in line:
                        return line.split(":", 1)[1].strip()
        except Exception:
            return platform.processor()
    else:
        return platform.processor()

def get_cpu_max_freq():
    """获取CPU最大频率"""
    if sys.platform.startswith("win"):
        try:
            # 使用 PowerShell 获取 CPU 最大频率
            result = subprocess.run(
                ["powershell", "-Command", "Get-WmiObject -Class Win32_Processor | Select-Object -ExpandProperty MaxClockSpeed"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0 and result.stdout.strip():
                max_freq_mhz = float(result.stdout.strip())
                return max_freq_mhz / 1000  # 转换为GHz
        except (subprocess.TimeoutExpired, FileNotFoundError, ValueError, Exception):
            pass
        
        try:
            # 回退到 WMIC 命令
            result = subprocess.run(
                ["wmic", "cpu", "get", "MaxClockSpeed", "/format:value"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if line.startswith('MaxClockSpeed='):
                        freq_str = line.split('=', 1)[1].strip()
                        if freq_str and freq_str.isdigit():
                            return int(freq_str) / 1000  # 转换为GHz
        except (subprocess.TimeoutExpired, FileNotFoundError, ValueError, Exception):
            pass
        
        # 回退到 psutil
        cpu_freq = psutil.cpu_freq()
        if cpu_freq and cpu_freq.max:
            return cpu_freq.max / 1000
            
    elif sys.platform.startswith("linux"):
        try:
            # 尝试从 /proc/cpuinfo 获取CPU频率信息
            with open("/proc/cpuinfo", "r") as f:
                for line in f:
                    if "cpu MHz" in line:
                        freq_mhz = float(line.split(":", 1)[1].strip())
                        return freq_mhz / 1000  # 转换为GHz
            
            # 尝试从 /sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq 获取
            try:
                with open("/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq", "r") as f:
                    max_freq_khz = int(f.read().strip())
                    return max_freq_khz / 1000000  # 转换为GHz
            except FileNotFoundError:
                pass
                
        except Exception:
            pass
    
    # 回退到 psutil
    cpu_freq = psutil.cpu_freq()
    if cpu_freq and cpu_freq.max:
        return cpu_freq.max / 1000
    return None

def get_os_info():
    """获取操作系统信息"""
    if sys.platform.startswith("linux"):
        try:
            # 尝试读取 /etc/os-release 获取发行版信息
            with open("/etc/os-release", "r") as f:
                lines = f.readlines()
                os_info = {}
                for line in lines:
                    if "=" in line:
                        key, value = line.strip().split("=", 1)
                        os_info[key] = value.strip('"')
                
                # 构建发行版信息
                if "PRETTY_NAME" in os_info:
                    return os_info["PRETTY_NAME"]
                elif "NAME" in os_info and "VERSION" in os_info:
                    return f"{os_info['NAME']} {os_info['VERSION']}"
                else:
                    return f"Linux {platform.release()}"
        except FileNotFoundError:
            return f"Linux {platform.release()}"
    else:
        return f"{platform.system()} {platform.release()} {platform.version()}"

def get_system_info():
    os_info = get_os_info()
    cpu_model = get_cpu_name()
    cpu_max_freq = get_cpu_max_freq()
    cpu_freq = psutil.cpu_freq()
    cpu_usage = psutil.cpu_percent(interval=1)
    # 确保CPU使用率不显示负数
    cpu_usage = max(0, cpu_usage)
    mem = psutil.virtual_memory()
    mem_total = mem.total / (1024 ** 3)
    mem_used = mem.used / (1024 ** 3)
    mem_percent = mem.percent

    print(f"操作系统: {os_info}")
    if cpu_max_freq:
        print(f"CPU型号: {cpu_model} @{cpu_max_freq:.2f} GHz")
    else:
        print(f"CPU型号: {cpu_model}")
    print(f"CPU核心数: {psutil.cpu_count(logical=False)} 物理核心 / {psutil.cpu_count(logical=True)} 逻辑核心")
    if cpu_freq and cpu_freq.current:
        print(f"CPU频率: {cpu_freq.current/1000:.2f} GHz")
    else:
        print("CPU频率: 未知")
    print(f"CPU使用率: {cpu_usage:.2f} %")
    print(f"内存总量: {mem_total:.2f} GB")
    print(f"内存已用: {mem_used:.2f} GB")
    print(f"内存使用率: {mem_percent:.2f} %")
    # 硬盘信息
    disk_partitions = psutil.disk_partitions()
    print("\n硬盘信息:")
    processed_devices = set()  # 避免重复显示同一设备
    for partition in disk_partitions:
        # 跳过已处理的设备和特殊文件系统
        if (partition.device in processed_devices or 
            partition.fstype in ['tmpfs', 'devtmpfs', 'sysfs', 'proc', 'cgroup', 'cgroup2']):
            continue
        try:
            partition_usage = psutil.disk_usage(partition.mountpoint)
            disk_total = partition_usage.total / (1024 ** 3)
            disk_used = partition_usage.used / (1024 ** 3)
            disk_free = partition_usage.free / (1024 ** 3)
            disk_percent = (disk_used / disk_total) * 100
            print(f"  {partition.device} 挂载点:{partition.mountpoint} ({partition.fstype})")
            print(f"    总容量: {disk_total:.2f} GB")
            print(f"    已使用: {disk_used:.2f} GB ({disk_percent:.1f}%)")
            print(f"    可用空间: {disk_free:.2f} GB")
            processed_devices.add(partition.device)
        except PermissionError:
            print(f"  {partition.device} - 无法访问")

def get_system_info_dict():
    """
    获取系统信息并返回字典格式
    
    Returns:
        dict: 包含系统信息的字典
    """
    os_info = get_os_info()
    cpu_model = get_cpu_name()
    cpu_max_freq = get_cpu_max_freq()
    cpu_freq = psutil.cpu_freq()
    cpu_usage = psutil.cpu_percent(interval=1)
    cpu_usage = max(0, cpu_usage)
    mem = psutil.virtual_memory()
    
    # 硬盘信息
    disk_info = []
    disk_partitions = psutil.disk_partitions()
    processed_devices = set()
    
    for partition in disk_partitions:
        if (partition.device in processed_devices or 
            partition.fstype in ['tmpfs', 'devtmpfs', 'sysfs', 'proc', 'cgroup', 'cgroup2']):
            continue
        try:
            partition_usage = psutil.disk_usage(partition.mountpoint)
            disk_info.append({
                'device': partition.device,
                'mountpoint': partition.mountpoint,
                'fstype': partition.fstype,
                'total_gb': round(partition_usage.total / (1024 ** 3), 2),
                'used_gb': round(partition_usage.used / (1024 ** 3), 2),
                'free_gb': round(partition_usage.free / (1024 ** 3), 2),
                'percent': round((partition_usage.used / partition_usage.total) * 100, 1)
            })
            processed_devices.add(partition.device)
        except PermissionError:
            disk_info.append({
                'device': partition.device,
                'error': '无法访问'
            })
    
    return {
        'os': os_info,
        'cpu': {
            'model': cpu_model,
            'max_freq_ghz': cpu_max_freq,
            'current_freq_ghz': cpu_freq.current/1000 if cpu_freq and cpu_freq.current else None,
            'usage_percent': cpu_usage,
            'physical_cores': psutil.cpu_count(logical=False),
            'logical_cores': psutil.cpu_count(logical=True)
        },
        'memory': {
            'total_gb': round(mem.total / (1024 ** 3), 2),
            'used_gb': round(mem.used / (1024 ** 3), 2),
            'percent': mem.percent
        },
        'disks': disk_info
    }

def print_system_info():
    """打印格式化的系统信息（原来的 get_system_info 函数）"""
    get_system_info()

# 保持向后兼容
def main():
    """主函数，打印系统信息"""
    get_system_info()

if __name__ == "__main__":
    main()