# ObtainerCLI DataMixer Index Runtime

日期：2026-06-30
状态：DataMixer-only

DataMixer index 是 Obtainer 的生产索引与召回入口：

```bash
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse index build --json
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse index stats --json
loopai-obtainercli dm --root /data/lakes/code_sft/warehouse recall \
  --query "buggy and fixed Python code pairs for runtime exception repair" \
  --filter "domain = 'code' AND task_type = 'SFT'" \
  --limit 50 \
  --json
```

`index build` 会基于 DataMixer catalog 和 CAS 内容构建本地向量索引与
SQLite FTS5 全文索引。需要外部 embedding 服务或模型池标注时，使用
DataMixer operator 或 pipeline 把结果写回 catalog，然后再运行 index/recall
检查覆盖。

本仓库仍保留 OpenAI-compatible embedding server 脚本，供 DataMixer operator
或外部处理流程调用：

```bash
python scripts/obtainercli_embedding_server.py \
  --host 127.0.0.1 \
  --port 8000
```

验证服务：

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

生产数据覆盖检查应以 DataMixer `index stats`、`recall` 和 recipe plan 结果为准。
