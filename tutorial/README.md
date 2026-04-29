# LoopAI Tutorial Docs

This directory contains the VitePress documentation site for introducing LoopAI.

## Recommended Environment

1. Check Node.js version

```bash
node -v
```

It is recommended to use Node.js `20.x`.

2. Check Yarn

```bash
yarn -v
```

If Yarn is not installed yet, you can enable it with Corepack:

```bash
corepack enable
corepack prepare yarn@stable --activate
yarn -v
```

## Project Setup

Install dependencies:

```bash
yarn install
```

## Run Docs In Development

Start the VitePress docs site locally:

```bash
yarn docs:dev
```

Default local address:

```text
http://localhost:5174
```

## Build Docs

Build the static site:

```bash
yarn docs:build
```

## Preview Production Build

Preview the built site locally:

```bash
yarn docs:preview
```

Default preview address:

```text
http://localhost:4174
```

## Directory Structure

```text
tutorial/
├── docs/
│   ├── .vitepress/     # VitePress site config
│   ├── guide/          # Tutorial pages
│   ├── public/         # Static assets such as images
│   └── index.md        # Home page
├── package.json
└── README.md
```

## Main Content Areas

- `docs/index.md`: LoopAI overview
- `docs/guide/webui-tutorial.md`: WebUI tutorial
- `docs/guide/cli-tutorial.md`: CLI tutorial
- `docs/guide/details/`: Per-agent detailed guides

## Notes

- Put screenshots and static images under `docs/public/`
- WebUI tutorial images are currently stored under `docs/public/images/webui/`
