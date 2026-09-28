# API Key 配置

lite 版本的 Obtainer 只从 Hugging Face Hub 搜索和获取数据集。公开数据集
无需凭据；如果使用私有或 gated 数据集，请通过标准环境变量
`HF_TOKEN`/`HUGGINGFACEHUB_API_TOKEN` 提供 Hugging Face token，并遵守该数据集
的访问条款。

网页搜索和 Kaggle 凭据不属于 lite 数据集获取流程，不应再配置或用于 acquisition。

## 安全注意事项

- 不要把 token 提交到 Git 或写入已跟踪的配置文件。
- 优先使用环境变量或本地 secret manager。
- token 泄露后应立即轮换。

官方文档：<https://huggingface.co/docs/hub/security-tokens>
