# LiveCodeBench 判分镜像（源码在 src/，vendored 自上游 + Judger 的改动）。
# 生成交给外部 OpenAI 兼容的 vLLM（LCB 的 --vllm_base_url），所以镜像里不装
# torch/vllm：体积小、不需要 GPU，生成和判分都在容器里跑。
#
# 构建上下文就是本目录；Judger 第一次用到时会自动 docker build（见
# utils/evaluate_code.py 的 _ensure_livecodebench_image），也可以手动：
#   docker build --network host -t livecodebench:latest \
#       --build-arg PIP_INDEX_URL=<内网源> loopai/skills/Judger/docker/livecodebench
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TOKENIZERS_PARALLELISM=false

WORKDIR /app

# PyPI 不可达时用 --build-arg PIP_INDEX_URL=<内网源>
ARG PIP_INDEX_URL=https://pypi.org/simple

# datasets: 读本地题目文件；openai: 回调 vLLM；pebble/attrs: LCB 的多进程助手；
# numpy/pandas: 评测器本身和被它执行的生成代码都会用到。
RUN pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" \
      "datasets>=3.2.0" \
      "openai>=1.59.6" \
      "pebble>=5.1.0" \
      "attrs" \
      "numpy" \
      "pandas" \
      "tqdm" \
      "setuptools"

COPY src/pyproject.toml src/README.md ./
COPY src/lcb_runner ./lcb_runner
RUN pip install --no-cache-dir --no-deps --index-url "${PIP_INDEX_URL}" -e .

# 生成的样本、缓存和指标都相对工作目录写；宿主机把 bench 目录挂到 /app/output。
VOLUME ["/app/output", "/app/cache"]

ENTRYPOINT ["python", "-m", "lcb_runner.runner.main"]
