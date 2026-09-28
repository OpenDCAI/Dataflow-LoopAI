# API Key Setup

The lite Obtainer workflow acquires datasets exclusively from the Hugging Face
Hub. Public Hub datasets do not require credentials. If your deployment uses a
private or gated dataset, provide the Hugging Face token through the standard
`HF_TOKEN`/`HUGGINGFACEHUB_API_TOKEN` environment variable and follow the
dataset's access terms.

Web-search and Kaggle credentials are not part of the lite acquisition path and
must not be configured for dataset collection.

## Security

- Do not commit tokens to Git or put them in checked-in configuration files.
- Prefer environment variables or a local secret manager.
- Rotate a token immediately if it is exposed.

Official reference: <https://huggingface.co/docs/hub/security-tokens>
