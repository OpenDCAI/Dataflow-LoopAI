# LiveCodeBench 判分镜像

`format_type=livecodebench` 的 code bench 用的评测镜像。和 evalplus 分支分工不同：
**生成和判分都在容器里**，容器通过 `--vllm_base_url` 回调 Judger 在宿主机起的 vLLM；
镜像里没有 torch/vllm，500 MB 上下，不需要 GPU。

| 文件 | 作用 |
| --- | --- |
| `Dockerfile` + `.dockerignore` | 构建上下文就是本目录 |
| `src/` | vendored 的上游 LiveCodeBench 源码（含 Judger 的改动），镜像是拿它 `pip install -e .` |
| `UPSTREAM` | `src/` 对应的上游仓库和 commit |

`src/` 相对上游那个 commit 的改动：`--vllm_base_url` / `--vllm_api_key` / `--model_style` /
`--local_dataset_path` / `--enable_thinking` 五个参数，本地 jsonl 的 `contest_date` 兼容
（上游只认 ISO 字符串，本地导出的那份是 epoch 毫秒），以及 torch / anthropic 改成可选导入
（镜像里不装这两样）。

## 构建

Judger 第一次用到时自动构建：`_ensure_livecodebench_image()`（`utils/evaluate_code.py`）
先 `docker image inspect`，缺了就 `docker build --network host`，并把宿主机的
`PIP_INDEX_URL` / `PIP_TRUSTED_HOST` / `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY`
透传成 `--build-arg` —— 和数学评测镜像（`docker/math_eval`）同一套做法。

手动构建：

```bash
docker build --network host -t livecodebench:latest \
    --build-arg PIP_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple \
    loopai/skills/Judger/docker/livecodebench
```

镜像名用 `LCB_EVAL_IMAGE` 覆盖（默认 `livecodebench:latest`）。跨机器搬运：

```bash
docker save livecodebench:latest | gzip > livecodebench.tgz   # 约 200MB
docker load -i livecodebench.tgz                              # 目标机
```

## 升级上游

1. 拿一份上游 LiveCodeBench 的 checkout，在它上面重新做我们的改动。
2. 用新源码覆盖 `src/`（`lcb_runner/` + `pyproject.toml` + `README.md` + `LICENSE`），
   并把新 commit 写进 `UPSTREAM`。
3. 重新构建（`docker rmi livecodebench:latest` 后再跑一次评测，或手动 build），
   跑单测 `tests/test_code_eval_container.py`，再跑一次真实评测。
