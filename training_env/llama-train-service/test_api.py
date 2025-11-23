#!/usr/bin/env python
"""
测试脚本：验证API服务是否正常工作
"""

import requests
import json
import time
import sys

BASE_URL = "http://localhost:8000"

def test_health_check():
    """测试健康检查接口"""
    print("🔍 Testing health check...")
    try:
        response = requests.get(f"{BASE_URL}/health")
        if response.status_code == 200:
            print("✅ Health check passed")
            return True
        else:
            print(f"❌ Health check failed: {response.status_code}")
            return False
    except requests.exceptions.RequestException as e:
        print(f"❌ Cannot connect to service: {e}")
        return False

def test_training_api():
    """测试训练API"""
    print("\n🚀 Testing training API...")
    
    # 示例配置
    config_content = """
model_name: llama3
model_name_or_path: /path/to/llama3

dataset: alpaca_gpt4_en
template: llama3

stage: sft
do_train: true
finetuning_type: lora
lora_target: q_proj,v_proj

dataset_dir: data
cutoff_len: 1024
max_samples: 100

output_dir: saves/test_run
num_train_epochs: 1.0
per_device_train_batch_size: 1
learning_rate: 5.0e-5
"""
    
    # 发送训练请求
    payload = {
        "config": config_content.strip(),
        "task_name": "API测试任务"
    }
    
    try:
        response = requests.post(f"{BASE_URL}/train", json=payload)
        
        if response.status_code == 200:
            result = response.json()
            task_id = result['task_id']
            print(f"✅ Training task started: {task_id}")
            return task_id
        else:
            print(f"❌ Training API failed: {response.status_code}")
            print(f"Response: {response.text}")
            return None
            
    except requests.exceptions.RequestException as e:
        print(f"❌ Training API error: {e}")
        return None

def test_status_api(task_id):
    """测试状态查询API"""
    print(f"\n📊 Testing status API for task: {task_id}")
    
    try:
        response = requests.get(f"{BASE_URL}/status/{task_id}")
        
        if response.status_code == 200:
            result = response.json()
            print(f"✅ Task status: {result['status']}")
            print(f"   Created: {result['created_at']}")
            if result.get('started_at'):
                print(f"   Started: {result['started_at']}")
            return True
        else:
            print(f"❌ Status API failed: {response.status_code}")
            return False
            
    except requests.exceptions.RequestException as e:
        print(f"❌ Status API error: {e}")
        return False

def test_logs_api(task_id):
    """测试日志查询API"""
    print(f"\n📝 Testing logs API for task: {task_id}")
    
    try:
        response = requests.get(f"{BASE_URL}/logs/{task_id}?max_lines=50")
        
        if response.status_code == 200:
            result = response.json()
            print(f"✅ Logs retrieved: {result['total_lines']} total lines")
            if result['logs']:
                print("   Recent logs:")
                lines = result['logs'].split('\n')[-5:]  # 显示最后5行
                for line in lines:
                    if line.strip():
                        print(f"   > {line}")
            return True
        else:
            print(f"❌ Logs API failed: {response.status_code}")
            return False
            
    except requests.exceptions.RequestException as e:
        print(f"❌ Logs API error: {e}")
        return False

def test_tasks_list_api():
    """测试任务列表API"""
    print("\n📋 Testing tasks list API...")
    
    try:
        response = requests.get(f"{BASE_URL}/tasks")
        
        if response.status_code == 200:
            result = response.json()
            print(f"✅ Tasks list retrieved: {result['total']} tasks")
            return True
        else:
            print(f"❌ Tasks list API failed: {response.status_code}")
            return False
            
    except requests.exceptions.RequestException as e:
        print(f"❌ Tasks list API error: {e}")
        return False

def main():
    """主测试函数"""
    print("🧪 LLaMA Factory Training Service API Tests")
    print("=" * 50)
    
    # 健康检查
    if not test_health_check():
        print("\n❌ Service is not running. Please start the service first:")
        print("python start.py")
        return 1
    
    # 测试训练API
    task_id = test_training_api()
    if not task_id:
        return 1
    
    # 等待一小段时间
    print("\n⏳ Waiting for task to initialize...")
    time.sleep(2)
    
    # 测试状态API
    test_status_api(task_id)
    
    # 测试日志API
    test_logs_api(task_id)
    
    # 测试任务列表API
    test_tasks_list_api()
    
    # 取消任务（避免浪费资源）
    print(f"\n🛑 Cancelling test task: {task_id}")
    try:
        response = requests.delete(f"{BASE_URL}/tasks/{task_id}")
        if response.status_code == 200:
            print("✅ Task cancelled successfully")
        else:
            print(f"⚠️ Task cancellation may have failed: {response.status_code}")
    except Exception as e:
        print(f"⚠️ Task cancellation error: {e}")
    
    print("\n✅ All tests completed!")
    return 0

if __name__ == "__main__":
    sys.exit(main())
