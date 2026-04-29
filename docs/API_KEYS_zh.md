# API Key 配置说明

本文说明如何获取项目根目录 `starter.yaml` 中的以下字段：

```yaml
system:
  tavily_api_key: ""
  kaggle_username: ""
  kaggle_key: ""
```

简体中文 | [English](./API_KEYS.md)

## 1. `tavily_api_key`

用于 Tavily 网络搜索。

获取方式：

1. 打开 https://www.tavily.com/，登录或注册账号。
2. 进入 Tavily 控制台：https://app.tavily.com/home
3. 创建或复制你的 API Key。
4. 将完整值填写到 `starter.yaml`：

```yaml
system:
  tavily_api_key: "tvly-..."
```

注意事项：

- Tavily Key 通常以 `tvly-` 开头。
- 本仓库中的部分示例脚本也支持使用 `TAVILY_API_KEY` 环境变量，或使用 `examples/scripts/tavily_api_key.txt` 作为备用来源。

## 2. `kaggle_username`

用于 Kaggle 数据集下载和身份认证。

获取方式：

1. 打开 https://www.kaggle.com/，登录或注册账号。
2. 打开账号设置页：https://www.kaggle.com/settings
3. 在 `API` 或 `Legacy API Credentials` 区域点击 `Create Legacy API Key`。
4. Kaggle 会下载一个 `kaggle.json` 文件。
5. 将该文件中的 `username` 字段复制到 `starter.yaml`：

```json
{
  "username": "your-kaggle-username",
  "key": "your-kaggle-api-key"
}
```

```yaml
system:
  kaggle_username: "your-kaggle-username"
```

## 3. `kaggle_key`

这是同一个 `kaggle.json` 文件中的 Kaggle API token。

将 `key` 字段的值填写到 `starter.yaml`：

```yaml
system:
  kaggle_key: "your-kaggle-api-key"
```

注意事项：

- 本仓库中的部分流程也支持使用 `KAGGLE_USERNAME` 和 `KAGGLE_KEY` 环境变量。
- 如果你已经有 `~/.kaggle/kaggle.json`，其中的内容就是同类凭据。

## 4. 安全注意事项

- 不要将真实 API Key 提交到 Git。
- 保持 `starter.yaml` 仅在本地使用，或在本地运行时使用环境变量。
- 如果 Key 已泄露，请立即在 Tavily 控制台或 Kaggle 设置页中轮换对应凭据。

## 官方参考

- Tavily 文档：https://docs.tavily.com/documentation/mcp
- Tavily 平台：https://app.tavily.com/home
- Kaggle 设置页：https://www.kaggle.com/settings
- Kaggle 官方客户端文档：https://github.com/Kaggle/kaggle-cli/blob/main/docs/README.md
