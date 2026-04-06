import subprocess
import psutil
import re

def get_nvidia_gpu_usage():
    try:
        result = subprocess.check_output(
            ['nvidia-smi', '--query-gpu=utilization.gpu', '--format=csv,nounits,noheader'],
            stderr=subprocess.STDOUT
        )
        usage = result.decode('utf-8').strip().split('\n')
        return True, [int(u) for u in usage]
    except FileNotFoundError:
        return False, "no detect nvidia-smi (maybe no NVIDIA GPU)"
    except subprocess.CalledProcessError as e:
        return False, f"call nvidia-smi failed: {e.output.decode('utf-8')}"

def get_huawei_npu_usage():
    try:
        result = subprocess.check_output(
            ['npu-smi', 'info'],
            stderr=subprocess.STDOUT
        )
        output = result.decode('utf-8')

        # 解析利用率（不同版本格式略有差异，这里做通用匹配）
        usage = re.findall(r'(\d+)%', output)

        # 一般前几个百分比是利用率（可能包含多个指标，这里简单返回）
        usage = [int(u) for u in usage]

        return True, usage

    except FileNotFoundError:
        return False, "no detect npu-smi (maybe no Huawei Ascend NPU)"
    except subprocess.CalledProcessError as e:
        return False, f"call npu-smi failed: {e.output.decode('utf-8')}"

def get_cpu_usage():
    # overall CPU usage
    total = psutil.cpu_percent(interval=None)
    
    # per-core usage
    per_core = psutil.cpu_percent(interval=None, percpu=True)
    
    return {
        "total": total,
        "per_core": per_core
    }

def get_memory_usage():
    mem = psutil.virtual_memory()
    
    return {
        "total": mem.total,        # 总内存（字节）
        "available": mem.available,# 可用内存
        "used": mem.used,          # 已用内存
        "free": mem.free,          # 空闲内存
        "percent": mem.percent     # 使用率 %
    }
