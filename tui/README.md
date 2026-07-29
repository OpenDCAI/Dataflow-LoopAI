# LoopAI TUI

A Vue + vue-tui terminal UI for LoopAI.

## Stack

- Vue 3 SFC
- `@vue-tui/runtime`
- `@vue-tui/vite`
- generated API client in `src/axios`

`dev` uses plain `vite`, not `@vue-tui/cli`, because `@vue-tui/vite` already provides the terminal dev runtime and is more stable in this project setup.

## Run

```bash
cd tui
yarn
yarn dev
```

## Build

```bash
yarn build
yarn start
```

## API Generation

```bash
yarn api
```

## Backend URL

Default:

```bash
http://127.0.0.1:8855
```

Override:

```bash
LOOPAI_API_BASE_URL=http://your-host:8855 yarn dev
```

## Keys

- `j` / `k`: move in task list
- `Enter`: refresh selected task
- `n`: create task
- `r`: rename selected task
- `d`: delete selected task
- `[` / `]`: previous or next node page
- `v`: open or close full conversation detail
- `Esc`: close prompt or detail panel
- `q`: quit
