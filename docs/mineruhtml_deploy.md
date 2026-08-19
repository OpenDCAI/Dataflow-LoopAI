# MinerU-HTML 部署教程（可复用，适用于任意 Linux + NVIDIA GPU 机器）

> 版本：MinerU-HTML v1.1（hunyuan0.5B-compact）
> 用途：HTML 主内容提取（网页正文 → Markdown），作为 DataMixer `webpage_to_pt` 正文提取算子的 HTTP 服务（默认端口 7986）。
> 本文是**通用教程**：不绑定任何一台机器的具体路径。所有命令使用统一的环境变量，先按目标机器修改一次（§0.2），即可整段复制执行。

---

## 0. 前置条件与路径规划（先看这一节）

### 0.1 前置条件
- Linux 系统，NVIDIA GPU（驱动可用，`nvidia-smi` 能正常输出）
- conda / miniconda（用于创建 Python 3.10 环境）
- 能访问外网：pip 与 HuggingFace 下载（国内可用镜像，见 §2.2 / §3.1）
- 目标端口（默认 `7986`）未被占用

### 0.2 统一路径变量（按你的机器改一次）

下文所有命令都引用这些变量，避免把路径写死。把 `<...>` 替换成目标机器的真实值：

```bash
# ================= 按你的机器修改 =================
export MINERU_PROJECT_DIR="<项目目录>"        # 例：/home/user/code/Dataflow-LoopAI（需含 scripts/ 目录）
export MINERU_CONDA_PREFIX="<conda 环境路径>" # 例：/opt/conda/envs/mineruhtml
export MINERU_MODEL_DIR="<模型存放目录>"       # 例：/home/user/models/MinerU-HTML-v1.1-hunyuan0.5B-compact
export MINERU_PORT="7986"                      # 服务端口
export MINERU_GPU="0"                          # 物理 GPU 编号（CUDA_VISIBLE_DEVICES）
export MINERU_LOG_DIR="<日志目录>"             # 例：/var/log/mineruhtml
# =================================================

mkdir -p "$MINERU_LOG_DIR"
```

> 建议把上面这段保存为 `mineruhtml.env`，每次新开 shell 用 `source mineruhtml.env` 加载，保证所有命令里的变量一致。

| 变量 | 含义 | 示例 |
|---|---|---|
| `MINERU_PROJECT_DIR` | 项目根目录（含 `scripts/mineruhtml_server.py`） | `/home/user/code/Dataflow-LoopAI` |
| `MINERU_CONDA_PREFIX` | conda 环境绝对路径（`conda env list` 可查） | `/opt/conda/envs/mineruhtml` |
| `MINERU_MODEL_DIR` | 模型目录 | `/home/user/models/MinerU-HTML-v1.1-hunyuan0.5B-compact` |
| `MINERU_PORT` | 服务端口 | `7986` |
| `MINERU_GPU` | 物理 GPU 编号 | `0` |
| `MINERU_LOG_DIR` | 日志目录 | `/var/log/mineruhtml` |

---

## 1. 概述

MinerU-HTML 是基于 SLM（Tencent Hunyuan 0.5B，256k 上下文）的 HTML 主内容提取工具：
- 从复杂网页 HTML 中**识别并提取正文**，过滤导航栏、广告、页脚、元信息等
- 输出 **Markdown**（`mm_md` 格式），可直接用于下游 PT/SFT 数据构建
- 本部署用 **vLLM** 作为推理后端，暴露 FastAPI 服务（`/health`、`/extract`）

### 架构
```
网页 HTML ──POST /extract──▶ FastAPI(mineruhtml_server.py)
                                └── mineru-html(MinerUHTMLGeneric)
                                      └── vLLM backend
                                            └── MinerU-HTML-v1.1-hunyuan0.5B-compact (GPU)
```

### 与项目的对应关系
| 组件 | 位置 |
|---|---|
| Conda 环境 | `$MINERU_CONDA_PREFIX`（Python 3.10） |
| 模型目录 | `$MINERU_MODEL_DIR` |
| 启动脚本 | `$MINERU_PROJECT_DIR/scripts/mineruhtml_server.py`（vLLM HTTP 服务） |
| 管理脚本 | `$MINERU_PROJECT_DIR/scripts/mineruhtml.sh`（start/stop/status） |
| 服务端口 | `$MINERU_PORT`（与 DataMixer `mineru_url` 对应） |

---

## 2. 环境准备

### 2.1 创建 Conda 环境（Python 3.10）
```bash
conda create -n mineruhtml python=3.10 -y
conda activate mineruhtml

# 记住环境绝对路径，填到 §0.2 的 MINERU_CONDA_PREFIX
conda env list
```

### 2.2 安装依赖
```bash
# 核心推理栈（注意版本匹配，vLLM 0.11.x 需 torch>=2.8）
pip install torch==2.9.0 torchvision==0.24.0 torchaudio==2.9.0
pip install "vllm==0.11.1" "transformers>=4.57"
pip install "mineru-html==1.1.2" "mineru-webkit==0.1.6"
pip install fastapi uvicorn pydantic
```
> 若 pip 慢，可加 `-i https://pypi.tuna.tsinghua.edu.cn/simple`；HuggingFace 下载慢用 `hf-mirror.com`（见下）。

---

## 3. 模型下载

模型仓库：**`opendatalab/MinerU-HTML-v1.1-hunyuan0.5B-compact`**（HuggingFace）
> 这是 Tencent Hunyuan 0.5B 的派生模型，compact 输出格式，支持 vLLM v1 backend，256k 上下文。

### 3.1 用 huggingface-cli 下载到本地
```bash
export HF_ENDPOINT=https://hf-mirror.com   # 国内镜像（可选但推荐）
export HF_HOME="$MINERU_MODEL_DIR/.hf_cache"

huggingface-cli download opendatalab/MinerU-HTML-v1.1-hunyuan0.5B-compact \
    --local-dir "$MINERU_MODEL_DIR"
```

### 3.2 模型文件清单（下载后应包含）
```
$MINERU_MODEL_DIR/
├── config.json            # HunYuanDenseV1ForCausalLM, hidden=1024, bf16
├── generation_config.json
├── model.safetensors      # 权重（约 1GB，bf16）
├── tokenizer.json / tokenizer_config.json
├── chat_template.jinja
├── special_tokens_map.json
├── README.md / LICENSE / NOTICE.txt
```
> 校验：`config.json` 中 `architectures: ["HunYuanDenseV1ForCausalLM"]`，`dtype: bfloat16`。

---

## 4. vLLM 部署（两种方式）

### 方式 A：`mineruhtml_server.py`（推荐，独立 FastAPI 服务）

#### 4.A.1 直接运行
```bash
"$MINERU_CONDA_PREFIX/bin/python" \
    "$MINERU_PROJECT_DIR/scripts/mineruhtml_server.py" \
    --model "$MINERU_MODEL_DIR" \
    --port "$MINERU_PORT" \
    --gpu "$MINERU_GPU" \
    --gpu-memory-utilization 0.3 \
    --max-context-window 8192 \
    --max-tokens 2048 \
    --fallback trafilatura \
    --output-format mm_md
```

**参数说明**
| 参数 | 默认 | 说明 |
|---|---|---|
| `--model` | 脚本内置默认路径（建议总是显式指定） | 本地模型目录（`$MINERU_MODEL_DIR`） |
| `--host` / `--port` | `0.0.0.0` / `7986` | 监听地址与端口 |
| `--gpu` | `0` | 物理 GPU 编号（`CUDA_VISIBLE_DEVICES`） |
| `--gpu-memory-utilization` | `0.3` | vLLM 显存占用比例（小模型 0.3 足够，避免挤占其他任务） |
| `--max-context-window` | `8192` | 模型上下文窗口（模型支持 256k，可按需调大） |
| `--max-tokens` | `2048` | 单次生成最大 token |
| `--fallback` | `trafilatura` | 提取失败时的兜底策略：`trafilatura` / `bypass` / `empty` |
| `--output-format` | `mm_md` | 输出格式（Markdown） |

> 该脚本也支持同名环境变量（未显式传参时读取）：`MINERU_HTML_MODEL`、`MINERU_HTML_HOST`、`MINERU_HTML_PORT`、`MINERU_HTML_GPU`、`MINERU_HTML_GPU_MEMORY_UTILIZATION`、`MINERU_HTML_MAX_CONTEXT_WINDOW`、`MINERU_HTML_MAX_TOKENS`、`MINERU_HTML_FALLBACK`、`MINERU_HTML_OUTPUT_FORMAT`。例如把 §0.2 的变量再映射一份后，可以不带参数直接启动，适合写进 systemd / supervisor：
> ```bash
> export MINERU_HTML_MODEL="$MINERU_MODEL_DIR"
> export MINERU_HTML_PORT="$MINERU_PORT"
> export MINERU_HTML_GPU="$MINERU_GPU"
> "$MINERU_CONDA_PREFIX/bin/python" "$MINERU_PROJECT_DIR/scripts/mineruhtml_server.py"
> ```

#### 4.A.2 后台启动
```bash
mkdir -p "$MINERU_LOG_DIR"
setsid nohup "$MINERU_CONDA_PREFIX/bin/python" \
    "$MINERU_PROJECT_DIR/scripts/mineruhtml_server.py" \
    --model "$MINERU_MODEL_DIR" \
    --port "$MINERU_PORT" --gpu "$MINERU_GPU" \
    > "$MINERU_LOG_DIR/mineruhtml.log" 2>&1 < /dev/null &
```

### 方式 B：`mineruhtml.sh`（dripper 服务，start/stop/status）

```bash
"$MINERU_PROJECT_DIR/scripts/mineruhtml.sh" start     # 启动（dripper.server，INFERENCE_BACKEND=vllm）
"$MINERU_PROJECT_DIR/scripts/mineruhtml.sh" status    # 查状态
"$MINERU_PROJECT_DIR/scripts/mineruhtml.sh" stop      # 停止
```
> 该脚本基于自身位置推断项目根目录（`scripts/` 的上一级），因此**整个项目目录拷到目标机器即可**，无需改路径。
> 它默认使用项目内 `envs/.mineruhtml`（Python 环境 + `dripper` 源码）和 `model/mineru-html` 子目录；端口、GPU、模型初始化参数可通过 `MINERUHTML_PORT`、`MINERUHTML_CUDA_VISIBLE_DEVICES`、`MINERUHTML_MODEL_INIT_KWARGS` 覆盖。
> 推荐用**方式 A**（conda env + `mineruhtml_server.py`，路径完全由 §0.2 变量控制），方式 B 适用于项目自带的 venv 结构。

---

## 5. 验证

### 5.1 健康检查
```bash
curl "http://127.0.0.1:$MINERU_PORT/health"
# {"status":"ok"}
```

### 5.2 提取测试
```bash
curl -X POST "http://127.0.0.1:$MINERU_PORT/extract" \
  -H 'Content-Type: application/json' \
  -d '{"html": "<html><body><h1>标题</h1><p>这是正文内容。</p><div class=\"ad\">广告</div></body></html>"}'
```
响应示例：
```json
{
  "main_html": "...",
  "main_content": "# 标题\n\n这是正文内容。\n",
  "error": null
}
```
> `main_content` 为提取后的 Markdown；提取失败时 `error` 非空（配合 `--fallback` 兜底）。

---

## 6. 与 LoopAI / DataMixer 集成

在 DataMixer 的 `starter.yaml`（或 pipeline.yaml）中配置：
```yaml
# 同机部署
mineru_url: http://127.0.0.1:7986

# 服务在其他机器时，改成该机器 IP（保证服务 --host 0.0.0.0 且防火墙放行端口）
# mineru_url: http://<服务机IP>:7986
```
- DataMixer 的 `webpage_to_pt` 算子（`mineru_transport: http`）会调用本服务的 `/extract`
- 网页采集 → MinerU-HTML 提取正文 → 生成 L1/L2 文本 → 进入后续 PT/SFT 流水线

---

## 7. 运维

| 操作 | 命令 |
|---|---|
| 查看日志 | `tail -f "$MINERU_LOG_DIR/mineruhtml.log"`（方式 A） |
| 停止 | 方式 A：`pkill -f mineruhtml_server.py`；方式 B：`"$MINERU_PROJECT_DIR/scripts/mineruhtml.sh" stop` |
| 换 GPU | 启动时改 `--gpu N`（或 `MINERUHTML_CUDA_VISIBLE_DEVICES`） |
| 显存紧张 | 调低 `--gpu-memory-utilization`（vLLM 会预留该比例） |

### 常见问题
| 现象 | 处理 |
|---|---|
| `/health` 无响应 | 检查端口是否被占：`ss -tlnp \| grep "$MINERU_PORT"`；看日志有无 vLLM 加载报错 |
| vLLM 加载 OOM | 降低 `--gpu-memory-utilization`，或换空闲 GPU |
| `/extract` 返回 500 | 看 `error` 字段；尝试 `--fallback bypass` 或换 `--output-format` |
| 中文网页提取差 | 调大 `--max-context-window`（模型支持 256k） |
| 远程访问不通 | 确认服务 `--host 0.0.0.0`、防火墙放行 `$MINERU_PORT`、调用方 `mineru_url` 使用服务机 IP |

---

## 8. 快速参考（一条命令部署）

```bash
# 0) 加载路径变量（见 §0.2，按你的机器修改）
source mineruhtml.env

# 1) 模型（已下载则跳过）
export HF_ENDPOINT=https://hf-mirror.com
huggingface-cli download opendatalab/MinerU-HTML-v1.1-hunyuan0.5B-compact \
    --local-dir "$MINERU_MODEL_DIR"

# 2) 启动（GPU $MINERU_GPU，端口 $MINERU_PORT）
mkdir -p "$MINERU_LOG_DIR"
setsid nohup "$MINERU_CONDA_PREFIX/bin/python" \
    "$MINERU_PROJECT_DIR/scripts/mineruhtml_server.py" \
    --model "$MINERU_MODEL_DIR" \
    --port "$MINERU_PORT" --gpu "$MINERU_GPU" \
    > "$MINERU_LOG_DIR/mineruhtml.log" 2>&1 < /dev/null &

# 3) 验证
curl "http://127.0.0.1:$MINERU_PORT/health"
```
