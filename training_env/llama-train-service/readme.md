# 🚀 LLaMA Factory 远程训练服务（FastAPI）

## 项目概述

这是一个基于FastAPI的远程训练服务，允许客户端通过API调用来触发LLaMA Factory训练任务，而无需在本地安装训练环境。

## 🎯 项目目标

将原本需要在本地执行的：

```
llamafactory-cli train examples/train_lora/llama3_lora_sft.yaml
```

改为：

✅ 部署为一个远程服务
✅ 客户端通过 API 上传 yaml 配置
✅ 服务端执行训练任务
✅ 客户端无需安装训练环境
✅ 返回训练进度 / 结果

## ✨ 项目已完成功能

- ✅ REST API接口
- ✅ 支持YAML配置文件上传（JSON和文件两种方式）
- ✅ 后台异步训练执行
- ✅ 任务状态查询
- ✅ 实时日志查看
- ✅ 任务管理（创建、查询、取消）
- ✅ 完整的错误处理
- ✅ 健康检查接口

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 启动服务

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

或者直接运行：

```bash
python -m app.main
```

### 3. 访问API文档

启动服务后访问：http://localhost:8000/docs

---

## ✅ 核心需求拆解

### 1. API 服务化

* 使用 FastAPI 提供 REST API
* 提供训练触发接口
* 上传/传递 YAML 配置文件
* 后端解析 YAML 并启动训练

### 2. 训练执行方式

调用：

```
llamafactory-cli train <config_path>
```

要求：

* 可以后台运行
* 不阻塞 API 请求
* 可管理多个训练任务

### 3. 环境隔离

客户端无需：

* CUDA
* PyTorch
* transformers
* LLaMA Factory 依赖

所有训练在服务器完成

### 4. 任务管理（基础版）

* 返回任务 ID
* 查询训练状态（可选）
* 日志输出（可选）

---

## ✅ 非目标（当前版本不做）

以下功能不包含在本版本：

* Web UI
* 分布式训练管理
* 多节点调度
* 自动模型部署
* 权限系统

---

# ✅ 项目功能概述

客户端调用：

```
POST /train
```

上传 YAML 或 YAML 内容

服务端：

1. 保存 YAML 到临时目录
2. 生成任务 ID
3. 后台执行：

```
llamafactory-cli train <yaml>
```

4. 返回：

```json
{
  "task_id": "abc123",
  "status": "started"
}
```

---

# ✅ API 设计

## POST /train

触发训练任务

### 请求格式（推荐 JSON 传内容）

```json
{
  "config": "model: ...\ntrain:\n  epochs: 3"
}
```

或 multipart 上传文件：

```
multipart/form-data
file: config.yaml
```

### 响应

```json
{
  "task_id": "abc123",
  "status": "started"
}
```

---

## GET /status/{task_id}

获取任务状态（基础版）

返回：

```json
{
  "task_id": "abc123",
  "status": "running"
}
```

---

## GET /logs/{task_id}

返回训练日志（可选）

---

# ✅ 服务端执行流程

```
FastAPI API
    ↓
接收 YAML
    ↓
保存为 config/abc123.yaml
    ↓
后台启动子进程:
    llamafactory-cli train config/abc123.yaml
    ↓
实时输出日志到 logs/abc123.log
```

---

# ✅ 技术方案

### FastAPI 负责：

* API 接口
* 任务管理
* 文件存储
* 返回状态

### Python subprocess 负责：

* 启动训练命令
* 捕获输出

示例执行方式：

```python
subprocess.Popen(
    ["llamafactory-cli", "train", config_path],
    stdout=logfile,
    stderr=logfile
)
```

---

# ✅ 项目目录结构（建议）

```
llama-train-service/
│
├── app/
│   ├── main.py              # FastAPI 入口
│   ├── tasks.py             # 启动训练子进程
│   ├── models.py            # Pydantic模型
│   ├── utils.py
│
├── configs/                 # 上传的yaml配置
├── logs/                    # 训练日志
├── runs/                    # 训练输出（模型等）
│
├── README.md
└── requirements.txt
```

---

# ✅ 运行方式

服务端：

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

客户端：

```
POST http://server:8000/train
```

---

# ✅ 依赖要求

服务器必须安装：

```
CUDA (可选)
PyTorch
LLaMA Factory
FastAPI
uvicorn
```

---

# ✅ 最小可用版本（MVP）

本项目最低目标：

✅ 一个 POST /train
✅ 后台执行训练
✅ 返回 task_id
✅ 有日志输出

---

# ✅ 扩展方向（代码 Agent 可实现）

可选增强：

* 训练队列
* 并发限制
* GPU 占用管理
* WebSocket 实时日志
* 自动模型部署

---

# ✅ 验收标准

执行：

```
curl -X POST http://host/train -d '{"config":"..."}'
```

能达到：

✅ 服务端启动训练
✅ 客户端无需安装训练环境
✅ 返回任务 ID
✅ 日志文件生成

即视为成功