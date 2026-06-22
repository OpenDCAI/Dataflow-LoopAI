# ObtainerCLI Embedding Runtime

## 默认行为

`loopai-obtainercli lake init` 默认写入以下配置：

```yaml
auto_embed: true
embedding_provider: openai-compatible
embedding_base_url: http://127.0.0.1:8000/v1
embedding_model: BAAI/bge-small-zh-v1.5
embedding_backend: local-jsonl
embedding_text_field: text
```

因此 `loopai-obtainercli ingest path` 在写入新 records 后，会自动调用
`/v1/embeddings` 并把向量写入 `embeddings` 表。传入 `--lake <lake_root>`
或 `--lake .loopai/lake.yaml` 都会读取同一份 lake 配置。

## 本地模型

当前已下载模型：

```text
/mnt/paper2any/xbr/loopai0531/models/BAAI/bge-small-zh-v1.5
```

模型是 BGE small Chinese embedding，输出维度为 512。服务端按模型自带
sentence-transformers 配置使用 CLS pooling，并做 L2 normalize。

## 推荐启动方式

当前开发机宿主环境已有 `torch 2.6.0+metax3.3.0.2`、`transformers`、
`fastapi` 和 `uvicorn`，推荐直接启动轻量 OpenAI-compatible 服务：

```bash
python scripts/obtainercli_embedding_server.py \
  --host 127.0.0.1 \
  --port 8000
```

可选环境变量：

```bash
export OBTAINERCLI_EMBED_MODEL_DIR=/mnt/paper2any/xbr/loopai0531/models/BAAI/bge-small-zh-v1.5
export OBTAINERCLI_EMBED_MODEL_NAME=BAAI/bge-small-zh-v1.5
export OBTAINERCLI_EMBED_DEVICE=auto
export OBTAINERCLI_EMBED_DTYPE=auto
export OBTAINERCLI_EMBED_MAX_LENGTH=512
```

## 验证 API

```bash
python - <<'PY'
import json, urllib.request

payload = json.dumps({
    "model": "BAAI/bge-small-zh-v1.5",
    "input": ["测试 embedding 接口"]
}).encode()
req = urllib.request.Request(
    "http://127.0.0.1:8000/v1/embeddings",
    data=payload,
    headers={"content-type": "application/json"},
    method="POST",
)
body = json.load(urllib.request.urlopen(req, timeout=120))
print(len(body["data"][0]["embedding"]))
PY
```

期望输出：

```text
512
```

## 入湖自动 embedding 验证

```bash
loopai-obtainercli lake init --root /tmp/obtainercli_lake --if-not-exists --json
loopai-obtainercli ingest path \
  --lake /tmp/obtainercli_lake \
  --input /path/to/records.jsonl \
  --dataset demo \
  --domain code \
  --processing-level pretrain_ready \
  --source-kind local \
  --json
```

如果 embedding 服务可用，输出里会包含：

```json
{"post_index": {"rows_indexed": 2, "embedding_model": "BAAI/bge-small-zh-v1.5"}}
```

如果服务不可用，入湖仍会成功，但状态为 `success_with_warnings`，warning
code 为 `POST_INDEX_EMBEDDING_FAILED`。

## vLLM 运行说明

仓库保留了 Docker/vLLM 启动脚本：

```bash
OBTAINERCLI_VLLM_IMAGE=<your-metax-vllm-image> \
scripts/obtainercli_vllm_embedding_server.sh
```

当前宿主 vLLM 0.11.0 + MetaX plugin 可以启动 `/v1/embeddings` 路由，但实测
BERT embedding 请求会触发 backend 错误：

- 默认 `FLASH_ATTN`：`flash_attn_varlen_func() got an unexpected keyword argument 'out'`
- `TRITON_ATTN`：BERT encoder self-attention 未实现
- `FLEX_ATTENTION`：torch inductor dynamic shape lowering 失败

因此当前机器上的可用默认 runtime 是 `scripts/obtainercli_embedding_server.py`。
如果你的 Docker 镜像内 vLLM backend 已修复，仍可用 vLLM 脚本提供相同的
OpenAI-compatible `/v1/embeddings` 接口，ObtainerCLI 不需要改配置。
