# 1. 确认只启动一次进程
pkill -f vllm.entrypoints.openai.api_server

# 2. 设置干净的多GPU NCCL 环境
export CUDA_VISIBLE_DEVICES=0,1
export NCCL_P2P_DISABLE=1
export NCCL_IB_DISABLE=1
export NCCL_DEBUG=INFO
export NCCL_SOCKET_IFNAME=lo   # 或 ens33 / eno1 等你的实际网卡名
export NCCL_BLOCKING_WAIT=1

python -m vllm.entrypoints.openai.api_server \
    --model /home/lpc/models/Qwen2.5-14B-Instruct/ \
    --port 8911 \
    --tensor-parallel-size 2 \
    --trust-remote-code \
    --gpu-memory-utilization 0.9 \
    --enable-auto-tool-choice \
    --tool-call-parser hermes
