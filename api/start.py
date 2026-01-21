#!/usr/bin/env python
"""
启动LLaMA Factory训练服务的脚本
"""

import os
import sys
import json
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
    
    if not os.path.exists('./app_config.json'):
        print("❌ app_config.json not found. Please copy from examples/app_config.json.example")
        return 1
    
    app_config = json.load(open('./app_config.json'))

    # LLaMA Factory项目目录
    llamafactory_dir = app_config['llamafactory_dir']
    llamafactory_env_path = app_config.get('llamafactory_env_path', '')
    
    # 检查LLaMA Factory目录是否存在
    if not os.path.exists(llamafactory_dir):
        print(f"❌ LLaMA Factory directory not found: {llamafactory_dir}")
        return 1
    
    # 从../examples/config/starter.yaml复制一份到./examples/starter.yaml
    starter_yaml = '../examples/config/starter.yaml'
    if not os.path.exists(starter_yaml):
        print(f"❌ starter.yaml not found in /examples/config/starter.yaml: {starter_yaml}")
        return 1
    try:
        subprocess.run(["cp", starter_yaml, "./examples/starter.yaml"], check=True)
        print("✅ starter.yaml copied to ./examples/starter.yaml")
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to copy starter.yaml: {e}")
        return 1
    
    # 检查是否安装了LLaMA Factory
    try:
        result = subprocess.run([os.path.join(llamafactory_env_path, "llamafactory-cli"), "help"], 
                              capture_output=True, text=True, timeout=300)
        if result.returncode == 0:
            print("✅ LLaMA Factory CLI found")
        else:
            print("❌ LLaMA Factory CLI not working properly")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        print("❌ LLaMA Factory CLI not found. Please install LLaMA Factory")
        return 1
    
    # 保存当前工作目录
    current_dir = os.getcwd()
    print(f"📂 Current directory: {current_dir}")
    print(f"📂 Switching to LLaMA Factory directory: {llamafactory_dir}")
    
    # 启动服务
    print("🚀 Starting LLaMA Factory Training Service...")
    print("📖 API Documentation: http://localhost:8000/docs")
    print("💡 Health Check: http://localhost:8000/health")
    
    try:
        # 切换到LLaMA Factory目录
        os.chdir(llamafactory_dir)
        print(f"✅ Changed working directory to: {os.getcwd()}")        # 启动uvicorn服务器
        cmd = [
            "uvicorn", 
            "app.main:app",
            "--host", "0.0.0.0", 
            "--port", "8000"
        ]
        
        print(f"🔧 Running FastAPI")
        
        # 设置环境变量，将当前项目目录和LLaMA Factory目录都加入PYTHONPATH
        env = os.environ.copy()
        pythonpath_parts = [current_dir, llamafactory_dir]
        if 'PYTHONPATH' in env:
            pythonpath_parts.append(env['PYTHONPATH'])
        env['PYTHONPATH'] = os.pathsep.join(pythonpath_parts)
        
        # 使用subprocess启动服务
        process = subprocess.Popen(
            cmd, 
            cwd=llamafactory_dir,  # 在LLaMA Factory目录中运行
            env=env
        )
        
        # 等待进程完成
        process.wait()
        
    except KeyboardInterrupt:
        print("\n✋ Service stopped by user")
        if 'process' in locals():
            process.terminate()
        return 0
    except Exception as e:
        print(f"❌ Error starting service: {e}")
        return 1
    finally:
        # 恢复原始工作目录
        try:
            os.chdir(current_dir)
            print(f"📂 Restored working directory to: {current_dir}")
        except Exception as e:
            print(f"⚠️ Warning: Could not restore working directory: {e}")

if __name__ == "__main__":
    sys.exit(main())
