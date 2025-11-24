#!/usr/bin/env python
"""
启动LLaMA Factory训练服务的脚本
"""

import os
import sys
import subprocess

def main():
    """主函数"""
    # 检查Python版本
    if sys.version_info < (3, 8):
        print("Error: Python 3.8+ is required")
        return 1
    
    # 检查是否安装了依赖
    try:
        import fastapi
        import uvicorn
        print("✅ FastAPI dependencies found")
    except ImportError:
        print("❌ FastAPI dependencies not found. Please install:")
        print("pip install -r requirements.txt")
        return 1
    
    # 检查是否安装了LLaMA Factory
    try:
        result = subprocess.run(["llamafactory-cli", "help"], 
                              capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            print("✅ LLaMA Factory CLI found")
        else:
            print("❌ LLaMA Factory CLI not working properly")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        print("❌ LLaMA Factory CLI not found. Please install LLaMA Factory")
        return 1
    
    # 启动服务
    print("🚀 Starting LLaMA Factory Training Service...")
    print("📖 API Documentation: http://localhost:8000/docs")
    print("💡 Health Check: http://localhost:8000/health")
    
    try:
        # 启动uvicorn服务器
        os.system("uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload")
    except KeyboardInterrupt:
        print("\n✋ Service stopped by user")
        return 0
    except Exception as e:
        print(f"❌ Error starting service: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
