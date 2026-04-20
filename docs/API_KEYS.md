# API Key Setup

This note explains where to obtain the following fields in the root `starter.yaml`:

```yaml
system:
  tavily_api_key: ""
  kaggle_username: ""
  kaggle_key: ""
```

## 1. `tavily_api_key`

Used for Tavily web search.

How to get it:

1. Open https://www.tavily.com/ and sign in or create an account.
2. Go to the Tavily dashboard: https://app.tavily.com/home
3. Create or copy your API key.
4. Fill the full value into `starter.yaml`:

```yaml
system:
  tavily_api_key: "tvly-..."
```

Notes:

- Tavily keys usually start with `tvly-`.
- Some example scripts in this repo also support `TAVILY_API_KEY` or `examples/scripts/tavily_api_key.txt` as a fallback.

## 2. `kaggle_username`

Used for Kaggle dataset download/authentication.

How to get it:

1. Open https://www.kaggle.com/ and sign in or create an account.
2. Open account settings: https://www.kaggle.com/settings
3. In the `API` or `Legacy API Credentials` section, click  `Create Legacy API Key`.
4. Kaggle will download a `kaggle.json` file.
5. Copy the `username` field from that file into `starter.yaml`:

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

This is the Kaggle API token from the same `kaggle.json` file.

Fill the `key` value into `starter.yaml`:

```yaml
system:
  kaggle_key: "your-kaggle-api-key"
```

Notes:

- This repo also supports the environment variables `KAGGLE_USERNAME` and `KAGGLE_KEY` in some flows.
- If you already have `~/.kaggle/kaggle.json`, its contents are the same kind of credentials.

## 4. Security Notes

- Do not commit real API keys to Git.
- Keep `starter.yaml` local, or use environment variables for local runs.
- If a key is exposed, rotate it from the Tavily dashboard or the Kaggle settings page immediately.

## Official References

- Tavily docs: https://docs.tavily.com/documentation/mcp
- Tavily platform: https://app.tavily.com/home
- Kaggle settings: https://www.kaggle.com/settings
- Kaggle official client docs: https://github.com/Kaggle/kaggle-cli/blob/main/docs/README.md
