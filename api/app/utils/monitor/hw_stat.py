import subprocess
import psutil
import re

def get_nvidia_gpu_usage():
    try:
        result = subprocess.check_output(
            [
                'nvidia-smi',
                '--query-gpu=utilization.gpu,memory.total,memory.used,memory.free',
                '--format=csv,nounits,noheader'
            ],
            stderr=subprocess.STDOUT
        )

        lines = result.decode('utf-8').strip().split('\n')

        gpus = []
        for idx, line in enumerate(lines):
            util, total, used, free = [int(x) for x in line.split(', ')]

            gpus.append({
                "gpu_id": idx,
                "utilization_percent": util,
                "memory": {
                    "total_MB": total,
                    "used_MB": used,
                    "free_MB": free,
                    "usage_percent": round(used / total * 100, 2)
                }
            })

        return True, gpus

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

        lines = output.split('\n')

        devices = []
        current_device = None

        for line in lines:
            line = line.strip()

            # 识别设备ID（不同版本可能是 NPU ID / Device）
            if re.search(r'NPU\s*\d+|Device\s*\d+', line):
                if current_device:
                    devices.append(current_device)

                device_id = re.findall(r'\d+', line)[0]
                current_device = {
                    "npu_id": int(device_id),
                    "utilization_percent": None,
                    "memory": {}
                }

            # 利用率（AI Core）
            if "Utilization" in line or "AICore" in line:
                match = re.search(r'(\d+)%', line)
                if match and current_device:
                    current_device["utilization_percent"] = int(match.group(1))

            # 显存（HBM）
            if "HBM" in line or "Memory" in line:
                nums = re.findall(r'\d+', line)
                if len(nums) >= 2 and current_device:
                    used = int(nums[0])
                    total = int(nums[1])

                    current_device["memory"] = {
                        "total_MB": total,
                        "used_MB": used,
                        "free_MB": total - used,
                        "usage_percent": round(used / total * 100, 2)
                    }

        # 最后一个设备补进去
        if current_device:
            devices.append(current_device)

        return True, devices

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
