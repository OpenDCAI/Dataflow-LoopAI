#!/usr/bin/env python
"""
Start LoopAI Server
"""

import os
import sys
import shutil
import subprocess
from omegaconf import OmegaConf


CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
CODEX_HOME_DIR = os.path.join(PROJECT_ROOT, "codex_home")
CODEX_HOME_EXAMPLE_DIR = os.path.join(PROJECT_ROOT, "examples", "codex_home_example")


def ensure_codex_home():
    """Ensure ./codex_home contains example seed files without deleting runtime state."""
    if not os.path.isdir(CODEX_HOME_EXAMPLE_DIR):
        raise FileNotFoundError(f"codex_home example directory not found: {CODEX_HOME_EXAMPLE_DIR}")

    os.makedirs(CODEX_HOME_DIR, exist_ok=True)
    for entry_name in os.listdir(CODEX_HOME_EXAMPLE_DIR):
        source_path = os.path.join(CODEX_HOME_EXAMPLE_DIR, entry_name)
        target_path = os.path.join(CODEX_HOME_DIR, entry_name)

        if os.path.isdir(source_path) and not os.path.islink(source_path):
            if os.path.exists(target_path):
                continue
            shutil.copytree(source_path, target_path)
        else:
            shutil.copy2(source_path, target_path)

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
    
    if not os.path.exists('./starter.yaml'):
        print("❌ starter.yaml not found. Please copy from examples/config/starter.yaml")
        return 1
    
    cfg = OmegaConf.load('./starter.yaml')
    system_config = cfg.get('system', {})
    default_states_config = cfg.get('default_states', {})
    trainer_config = default_states_config.get('trainer', {})
    api_port = system_config.get('api_port', 8855)

    # LLaMA Factory项目目录
    llamafactory_dir = trainer_config['llamafactory_dir']
    llamafactory_env_path = trainer_config.get('llamafactory_env_path', '')
    
    # 检查LLaMA Factory目录是否存在
    if not os.path.exists(llamafactory_dir):
        print(f"❌ LLaMA Factory directory not found: {llamafactory_dir}")
        return 1

    # 保存当前工作目录
    current_dir = os.getcwd()
    print(f"📂 Current directory: {current_dir}")
    print(f"📂 LLaMA Factory directory: {llamafactory_dir}")

    try:
        ensure_codex_home()
        print(f"✅ Ensured codex_home seed files from example: {CODEX_HOME_EXAMPLE_DIR}")
    except Exception as e:
        print(f"❌ Failed to prepare codex_home: {e}")
        return 1
    
    # 启动服务
    print("🚀 Starting LoopAI Service...")
    print(f"📖 API Documentation: http://localhost:{api_port}/docs")
    print(f"💡 Health Check: http://localhost:{api_port}/health")
    
    try:
        cmd = [
            sys.executable,
            "-m", "uvicorn",
            "api.app.main:app",
            "--host", "0.0.0.0",
            "--port", str(api_port),
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
            cwd=current_dir,
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
