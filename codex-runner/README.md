# codex-runner

Thin wrapper around `@openai/codex-sdk` that runs one Codex turn and streams the
result to stdout as newline-delimited JSON. Spawned by
`api/app/services/starter/codex.py`; all configuration arrives via environment
variables.

```bash
yarn build     # tsc -> dist/index.js
yarn test      # wrapper regression suite, no API calls
```

## Enabling Codex hooks (opt-in)

Hooks declared in `$CODEX_HOME/hooks.json` let a script inspect each command
**before** it runs and deny it with a reason that is fed back to the model. They
are the pre-execution checkpoint the guardrail work builds on.

Codex skips an untrusted hook **silently** — no error, no log line. Trust is
normally granted interactively in the TUI and persisted to `config.toml`, which
does not survive here because `_sync_codex_home_config()` rewrites that file on
every session. The CLI flag that waives the check (`--dangerously-bypass-hook-trust`)
cannot be passed through the SDK: `ThreadOptions` has no pass-through field.

So when `CODEX_ENABLE_HOOKS=1`, the runner points the SDK's public
`codexPathOverride` at `scripts/codex-hook-trust-wrapper.mjs`. The SDK spawns
that instead of the real binary; the wrapper injects the flag and execs the real
codex, restoring the vendored `PATH` entry the SDK skips when the binary path is
overridden.

| Variable | Effect |
|---|---|
| `CODEX_ENABLE_HOOKS=1` | Route through the wrapper so hooks run. Default: off, unchanged behaviour. |

The flag means "run enabled hooks without verifying their provenance". It does
**not** lower any permission — unlike `--dangerously-bypass-approvals-and-sandbox`,
which removes a gate rather than enabling one. Hook definitions are expected to
live in version control, so provenance comes from code review.

`runner.started` reports `enableHooks` and `codexPathOverride`, so a session's
log shows whether the wrapper was in the path.

### Deployment notes

- `scripts/codex-hook-trust-wrapper.mjs` **must keep its executable bit** (git
  mode `100755`). The SDK execs the path directly, so a lost bit surfaces as a
  bare `EACCES` from inside `child_process`; the runner pre-checks `X_OK` and
  fails with a `chmod +x` hint instead.
- `dist/` is gitignored — build on the deployment host.

### Tests

`yarn test` covers flag placement (plain and `resume` argv), `PATH` restoration,
exit-code propagation and signal forwarding, plus two canaries for contracts the
wrapper mirrors but does not own: that the bundled binary still advertises the
flag, and that the SDK still builds an argv beginning with `exec`. Both would
otherwise fail silently after an SDK or Codex upgrade.

`CODEX_REAL_BIN` swaps in a stub binary for these tests and is honoured only
under `NODE_ENV=test`.
