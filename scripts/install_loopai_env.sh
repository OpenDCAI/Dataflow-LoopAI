#!/usr/bin/env bash
# -*- coding: utf-8 -*-
#
# LoopAI 环境安装脚本
#
# 按 README「4.1 Installation」的口径装核心运行环境：
#   1. conda 建 Python 3.12 环境（默认名 loopai）
#   2. pip 装 uv，再 uv pip install -e . 装 LoopAI 本体 + 全部 Python 依赖
#   3. 可选：下载 WebUI dist、装 playwright 浏览器、装 codex-runner 依赖、
#      预拉 Judger 判分镜像
#   4. 自检：Python 版本、关键依赖、console script、codex / node / docker
#
# 用法：
#   bash scripts/install_loopai_env.sh                      # 装到 conda env "loopai"
#   bash scripts/install_loopai_env.sh --recreate           # 已存在就删掉重建
#   bash scripts/install_loopai_env.sh --skip-vllm          # 无 GPU / 只跑通核心：不装 vllm、torch
#   bash scripts/install_loopai_env.sh --with-ui --with-playwright
#   bash scripts/install_loopai_env.sh --check              # 只自检，不安装
#
# 说明：
#   * 内网 / 代理环境直接用现成的 http_proxy / https_proxy 环境变量即可，
#     pip 和 uv 都认；PyPI 镜像用 --index-url 指定。
#   * setup.py 里有 git+https 依赖（one-eval），所以机器上要有 git 且能访问 GitHub。
#   * --skip-vllm 时 vllm / torch 不装，Judger 的 code/math 判分跑不了，
#     其余（Configer / ObtainerCLI / Analyzer / Trainer 调度）不受影响。

set -euo pipefail

ENV_NAME="loopai"
PY_VERSION="3.12"
RECREATE=0
SKIP_VLLM=0
WITH_UI=0
WITH_PLAYWRIGHT=0
WITH_CODEX_RUNNER=0
WITH_EVALPLUS_IMAGE=0
CHECK_ONLY=0
INDEX_URL=""
EXTRA_INDEX_URL=""

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

C_INFO=$'\033[36m'; C_WARN=$'\033[33m'; C_ERR=$'\033[31m'; C_OK=$'\033[32m'; C_OFF=$'\033[0m'
info() { printf '%s[INFO]%s %s\n' "$C_INFO" "$C_OFF" "$*"; }
warn() { printf '%s[WARN]%s %s\n' "$C_WARN" "$C_OFF" "$*"; }
ok()   { printf '%s[ OK ]%s %s\n' "$C_OK"   "$C_OFF" "$*"; }
die()  { printf '%s[FAIL]%s %s\n' "$C_ERR"  "$C_OFF" "$*" >&2; exit 1; }

usage() {
    sed -n '3,/^$/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    cat <<'EOF'

开关：
  -n, --env-name NAME       conda 环境名（默认 loopai）
  -p, --python VERSION      环境 Python 版本（默认 3.12）
      --recreate            环境已存在时先删掉再建
      --skip-vllm           不装 vllm / torch（省几个 G，判分用不了）
      --with-ui             下载 WebUI dist 到 api/dist
      --with-playwright     装 playwright chromium（Obtainer 爬网页要用）
      --with-codex-runner   codex-runner 跑 yarn install + build
      --with-evalplus-image 预拉 Judger 判分镜像 ganler/evalplus:latest
      --index-url URL       PyPI 镜像（pip 和 uv 都用）
      --extra-index-url URL 额外的 PyPI 源
      --check               只做环境自检，不安装
  -h, --help                显示本帮助
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -n|--env-name)      ENV_NAME="${2:?--env-name 需要值}"; shift 2 ;;
        -p|--python)        PY_VERSION="${2:?--python 需要值}"; shift 2 ;;
        --recreate)         RECREATE=1; shift ;;
        --skip-vllm)        SKIP_VLLM=1; shift ;;
        --with-ui)          WITH_UI=1; shift ;;
        --with-playwright)  WITH_PLAYWRIGHT=1; shift ;;
        --with-codex-runner) WITH_CODEX_RUNNER=1; shift ;;
        --with-evalplus-image) WITH_EVALPLUS_IMAGE=1; shift ;;
        --index-url)        INDEX_URL="${2:?--index-url 需要值}"; shift 2 ;;
        --extra-index-url)  EXTRA_INDEX_URL="${2:?--extra-index-url 需要值}"; shift 2 ;;
        --check)            CHECK_ONLY=1; shift ;;
        -h|--help)          usage; exit 0 ;;
        *)                  die "未知参数：$1（用 --help 看用法）" ;;
    esac
done

PIP_ARGS=()
UV_ARGS=()
if [[ -n "$INDEX_URL" ]]; then
    PIP_ARGS+=(--index-url "$INDEX_URL")
    UV_ARGS+=(--index-url "$INDEX_URL")
fi
if [[ -n "$EXTRA_INDEX_URL" ]]; then
    PIP_ARGS+=(--extra-index-url "$EXTRA_INDEX_URL")
    UV_ARGS+=(--extra-index-url "$EXTRA_INDEX_URL")
fi

# ---------------------------------------------------------------------------
# 自检：装完（或 --check）后统一跑这里的检查
# ---------------------------------------------------------------------------
self_check() {
    local py_bin="$1"
    info "===== 环境自检 ====="

    "$py_bin" - <<'PY'
import importlib.metadata as md
import sys

print(f"Python      : {sys.version.split()[0]}  ({sys.executable})")

try:
    import loopai
    version = md.version("loopai")
    print(f"loopai      : OK  {version}  ({loopai.__file__})")
except Exception as exc:  # noqa: BLE001
    print(f"loopai      : MISSING  ({exc})")

# (标签, import 名, distribution 名 —— 后两个不一样时用得上，例如 open-dataflow → dataflow)
required = [
    ("langgraph", "langgraph", None),
    ("langchain", "langchain", None),
    ("fastapi", "fastapi", None),
    ("uvicorn", "uvicorn", None),
    ("tortoise", "tortoise", None),
    ("pydantic", "pydantic", None),
    ("datasets", "datasets", None),
    ("transformers", "transformers", None),
    ("tree_sitter", "tree_sitter", None),
    ("httpx", "httpx", None),
    ("omegaconf", "omegaconf", None),
    ("chromadb", "chromadb", None),
]
optional = [
    ("vllm", "vllm", None),
    ("torch", "torch", None),
    ("playwright", "playwright", None),
    ("open-dataflow", "dataflow", "open-dataflow"),
    ("one-eval", "one_eval", "one-eval"),
    ("func-timeout", "func_timeout", "func-timeout"),
]


def probe(module_name, dist_name):
    try:
        module = __import__(module_name)
    except Exception as exc:  # noqa: BLE001
        return "MISSING", str(exc).split("\n")[0]
    version = getattr(module, "__version__", None)
    if not version:
        for candidate in (dist_name, module_name):
            if not candidate:
                continue
            try:
                version = md.version(candidate)
                break
            except Exception:  # noqa: BLE001
                continue
    return "OK", version or "-"


for group, entries in (("必需", required), ("可选", optional)):
    for label, module_name, dist_name in entries:
        status, detail = probe(module_name, dist_name)
        mark = "OK  " if status == "OK" else "MISS"
        print(f"  [{mark}] {label:<16} {detail[:70]}")
PY

    info "console script："
    local entry
    for entry in loopai-judger loopai-configer loopai-analyzer loopai-trainer loopai-obtainercli; do
        if command -v "$entry" >/dev/null 2>&1; then
            printf '  [OK  ] %s\n' "$entry"
        else
            printf '  [MISS] %s   （重跑 uv pip install -e . 刷新 entry point）\n' "$entry"
        fi
    done

    info "外部工具："
    local tool hint
    for tool in git codex node yarn docker; do
        if command -v "$tool" >/dev/null 2>&1; then
            printf '  [OK  ] %-8s %s\n' "$tool" "$(command -v "$tool")"
        else
            case "$tool" in
                codex)  hint="curl -fsSL https://chatgpt.com/codex/install.sh | sh" ;;
                yarn)   hint="corepack enable（Node 16.10+ 自带）" ;;
                node)   hint="装 Node 20+" ;;
                docker) hint="Judger 判分需要 docker（没有则判分步骤报 DEPENDENCY_ERROR）" ;;
                *)      hint="" ;;
            esac
            printf '  [MISS] %-8s %s\n' "$tool" "$hint"
        fi
    done

    info "物料："
    local path
    for path in \
        "$REPO_ROOT/starter.yaml" \
        "$REPO_ROOT/codex_home" \
        "$REPO_ROOT/api/dist/index.html" \
        "$REPO_ROOT/tui/dist" \
        "$REPO_ROOT/codex-runner/node_modules"; do
        if [[ -e "$path" ]]; then
            printf '  [OK  ] %s\n' "$path"
        else
            printf '  [MISS] %s\n' "$path"
        fi
    done
}

if [[ "$CHECK_ONLY" == 1 ]]; then
    command -v conda >/dev/null 2>&1 || die "找不到 conda，无法定位环境 $ENV_NAME"
    # shellcheck disable=SC1091
    source "$(conda info --base)/etc/profile.d/conda.sh"
    conda env list | awk '{print $1}' | grep -qx "$ENV_NAME" || die "conda 环境 $ENV_NAME 不存在"
    set +u; conda activate "$ENV_NAME"; set -u
    self_check "$(command -v python)"
    exit 0
fi

# ---------------------------------------------------------------------------
# 1. conda 环境
# ---------------------------------------------------------------------------
command -v conda >/dev/null 2>&1 || die "找不到 conda；先装 miniconda：https://docs.conda.io/en/latest/miniconda.html"
command -v git >/dev/null 2>&1 || die "找不到 git；setup.py 里有 git+https 依赖（one-eval），必须有 git"

# shellcheck disable=SC1091
source "$(conda info --base)/etc/profile.d/conda.sh"

if conda env list | awk '{print $1}' | grep -qx "$ENV_NAME"; then
    if [[ "$RECREATE" == 1 ]]; then
        info "删除已存在的环境 $ENV_NAME ..."
        conda env remove -y -n "$ENV_NAME"
        conda create -y -n "$ENV_NAME" "python=$PY_VERSION"
    else
        info "环境 $ENV_NAME 已存在，直接复用（要重建加 --recreate）"
    fi
else
    info "创建环境 $ENV_NAME (python=$PY_VERSION) ..."
    conda create -y -n "$ENV_NAME" "python=$PY_VERSION"
fi

set +u; conda activate "$ENV_NAME"; set -u
PY_BIN="$(command -v python)"
ok "使用解释器：$PY_BIN（$("$PY_BIN" -V 2>&1)）"

# ---------------------------------------------------------------------------
# 2. 装 LoopAI 本体
# ---------------------------------------------------------------------------
info "安装 / 升级 pip + uv ..."
"$PY_BIN" -m pip install -U pip uv ${PIP_ARGS[@]+"${PIP_ARGS[@]}"}

if [[ "$SKIP_VLLM" == 1 ]]; then
    info "解析 setup.py 依赖（跳过 vllm / torch）..."
    "$PY_BIN" - "$TMP_DIR/requirements.txt" "$REPO_ROOT" <<'PY'
import ast
import pathlib
import sys

target = pathlib.Path(sys.argv[1])
tree = ast.parse((pathlib.Path(sys.argv[2]) / "setup.py").read_text(encoding="utf-8"))
requires = []
for node in ast.walk(tree):
    if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "setup":
        for kw in node.keywords:
            if kw.arg == "install_requires":
                requires = [ast.literal_eval(elt) for elt in kw.value.elts]
if not requires:
    sys.exit("没能从 setup.py 里解析出 install_requires")

kept = [req for req in requires if not req.lower().startswith(("vllm", "torch"))]
target.write_text("\n".join(kept) + "\n", encoding="utf-8")
print(f"  setup.py 共 {len(requires)} 条依赖，过滤掉 vllm/torch 后剩 {len(kept)} 条")
PY
    info "uv pip install -r <依赖清单> ..."
    "$PY_BIN" -m uv pip install --python "$PY_BIN" -r "$TMP_DIR/requirements.txt" ${UV_ARGS[@]+"${UV_ARGS[@]}"}
    info "uv pip install -e . --no-deps ..."
    "$PY_BIN" -m uv pip install --python "$PY_BIN" -e "$REPO_ROOT" --no-deps ${UV_ARGS[@]+"${UV_ARGS[@]}"}
    warn "已跳过 vllm / torch：Judger 的 code、math 判分跑不了，需要时手动装 vllm"
else
    info "uv pip install -e .（会带 vllm / torch，几个 G，耐心等）..."
    "$PY_BIN" -m uv pip install --python "$PY_BIN" -e "$REPO_ROOT" ${UV_ARGS[@]+"${UV_ARGS[@]}"}
fi
ok "LoopAI 本体安装完成"

# ---------------------------------------------------------------------------
# 3. 可选组件
# ---------------------------------------------------------------------------
if [[ "$WITH_PLAYWRIGHT" == 1 ]]; then
    info "安装 playwright chromium ..."
    if "$PY_BIN" -m playwright install chromium; then
        ok "playwright chromium 就绪"
    else
        warn "playwright chromium 安装失败；缺系统库时用：$PY_BIN -m playwright install --with-deps chromium"
    fi
fi

if [[ "$WITH_UI" == 1 ]]; then
    info "下载 WebUI dist 到 api/dist ..."
    if "$PY_BIN" "$REPO_ROOT/scripts/download_ui_release.py"; then
        ok "WebUI dist 就绪"
    else
        warn "WebUI dist 下载失败（内网 / 代理受限）；可手动下载 ui-v* Release 解压到 api/dist"
    fi
fi

if [[ "$WITH_CODEX_RUNNER" == 1 ]]; then
    if ! command -v node >/dev/null 2>&1; then
        warn "没装 node，跳过 codex-runner"
    elif ! command -v yarn >/dev/null 2>&1; then
        warn "没装 yarn，跳过 codex-runner（先执行 corepack enable）"
    else
        info "codex-runner: yarn install + build ..."
        (cd "$REPO_ROOT/codex-runner" && yarn && yarn build)
        ok "codex-runner 构建完成"
    fi
fi

if [[ "$WITH_EVALPLUS_IMAGE" == 1 ]]; then
    if command -v docker >/dev/null 2>&1; then
        info "预拉 Judger 判分镜像 ganler/evalplus:latest ..."
        docker pull ganler/evalplus:latest && ok "判分镜像就绪"
    else
        warn "没装 docker，跳过判分镜像"
    fi
fi

# ---------------------------------------------------------------------------
# 4. 配置模板
# ---------------------------------------------------------------------------
if [[ ! -e "$REPO_ROOT/starter.yaml" ]]; then
    info "starter.yaml 不存在，从 examples/config/starter.yaml 复制一份 ..."
    cp "$REPO_ROOT/examples/config/starter.yaml" "$REPO_ROOT/starter.yaml"
    "$PY_BIN" - "$REPO_ROOT" <<'PY'
import pathlib
import sys

import yaml

repo_root = pathlib.Path(sys.argv[1])
path = repo_root / "starter.yaml"
data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
system = data.setdefault("system", {})
system.setdefault("codex_workspace", str(repo_root))
system.setdefault("codex_home", str(repo_root / "codex_home"))
path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
print(f"  已写入 codex_workspace={repo_root}")
print(f"  已写入 codex_home={repo_root / 'codex_home'}")
PY
else
    info "starter.yaml 已存在，不动它"
fi

# ---------------------------------------------------------------------------
# 5. 自检 + 收尾
# ---------------------------------------------------------------------------
self_check "$PY_BIN"

cat <<EOF

${C_OK}安装流程结束${C_OFF}

下一步：
  1. conda activate $ENV_NAME
  2. 编辑 $REPO_ROOT/starter.yaml（api_port、model.pool 里的 api_key / base_url 必填）
  3. 起服务：python api/start.py      # WebUI http://localhost:8855 ，API 文档 /docs

提醒：
  * Judger 判分用 docker 跑官方镜像（缺失会自动 docker pull，拉不到会报错并给离线搬运命令）
  * Analyzer 需要能连上的 OpenAI 兼容 endpoint（analyzer.analyze_base_url）
  * codex（starter 交互入口）和 node/yarn（前端）不在本脚本安装范围内
EOF
