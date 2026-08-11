import { fileURLToPath } from "node:url";
import { accessSync, constants as fsConstants, existsSync } from "node:fs";
import { Codex, type ThreadEvent, type ThreadItem } from "@openai/codex-sdk";

type SandboxMode = "read-only" | "workspace-write" | "danger-full-access";
const MAX_COMMAND_OUTPUT_CHARS = Number(process.env.CODEX_EVENT_MAX_OUTPUT_CHARS ?? "12000");

const prompt = process.argv.slice(2).join(" ");
const timeoutMs = Number(process.env.CODEX_RUN_TIMEOUT_MS ?? "300000");
const workspace = process.env.CODEX_WORKSPACE;
const model = process.env.CODEX_MODEL ?? process.env.DEEPSEEK_MODEL;
const baseUrl = process.env.CODEX_BASE_URL ?? process.env.DEEPSEEK_BASE_URL;
const apiKey = process.env.CODEX_API_KEY ?? process.env.DEEPSEEK_API_KEY;
const codexHome = process.env.CODEX_HOME;
const sandboxMode = resolveSandboxMode(process.env.CODEX_SANDBOX_MODE);
const threadId = process.env.CODEX_THREAD_ID?.trim() || "";
const useProjectConfig = process.env.CODEX_USE_PROJECT_CONFIG === "1";
const enableHooks = process.env.CODEX_ENABLE_HOOKS === "1";
if (!prompt) {
    console.error("Usage: yarn dev \"your prompt\"");
    process.exit(1);
}

function getProcessEnv(): Record<string, string> {
    const env: Record<string, string> = {};

    for (const [key, value] of Object.entries(process.env)) {
        if (typeof value === "string") {
            env[key] = value;
        }
    }

    return env;
}

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
    return new Promise<T>((resolve, reject) => {
        const timer = setTimeout(() => {
            reject(new Error(`Codex run timed out after ${ms}ms`));
        }, ms);

        promise
            .then((value) => {
                clearTimeout(timer);
                resolve(value);
            })
            .catch((error) => {
                clearTimeout(timer);
                reject(error);
            });
    });
}

function writeStderr(payload: unknown): void {
    process.stderr.write(`${JSON.stringify(payload)}\n`);
}

function writeStdout(payload: unknown): void {
    process.stdout.write(`${JSON.stringify(payload)}\n`);
}

function truncateText(value: string, maxChars: number): string {
    if (value.length <= maxChars) {
        return value;
    }
    return `${value.slice(0, maxChars)}\n... [truncated ${value.length - maxChars} chars]`;
}

function sanitizeEvent(event: ThreadEvent): ThreadEvent {
    if (
        (event.type === "item.started" || event.type === "item.updated" || event.type === "item.completed")
        && event.item.type === "command_execution"
        && typeof event.item.aggregated_output === "string"
    ) {
        return {
            ...event,
            item: {
                ...event.item,
                aggregated_output: truncateText(event.item.aggregated_output, MAX_COMMAND_OUTPUT_CHARS)
            }
        };
    }
    return event;
}

/**
 * Hooks declared in `$CODEX_HOME/hooks.json` are skipped until they are trusted,
 * and the SDK cannot pass the CLI flag that waives that check. Overriding the
 * binary path with a wrapper that injects the flag is the only way to run hooks
 * on the SDK path without a machine-specific trust record.
 */
function resolveCodexPathOverride(): string | undefined {
    if (!enableHooks) {
        return undefined;
    }
    const wrapper = fileURLToPath(new URL("../scripts/codex-hook-trust-wrapper.mjs", import.meta.url));
    if (!existsSync(wrapper)) {
        throw new Error(`CODEX_ENABLE_HOOKS=1 but the hook-trust wrapper is missing: ${wrapper}`);
    }
    try {
        // The SDK execs this path directly, so a lost executable bit surfaces as
        // a bare EACCES from deep inside child_process.
        accessSync(wrapper, fsConstants.X_OK);
    } catch {
        throw new Error(`hook-trust wrapper is not executable, run: chmod +x ${wrapper}`);
    }
    return wrapper;
}

function resolveSandboxMode(value: string | undefined): SandboxMode {
    switch (value) {
        case "read-only":
        case "workspace-write":
        case "danger-full-access":
            return value;
        default:
            return "danger-full-access";
    }
}

async function main() {
    const codexPathOverride = resolveCodexPathOverride();

    writeStderr({
        type: "runner.started",
        cwd: process.cwd(),
        prompt,
        timeoutMs,
        workspace,
        model,
        baseUrl,
        apiKeySource: apiKey ? "env" : "default",
        codexHome,
        sandboxMode,
        threadId: threadId || null,
        useProjectConfig,
        enableHooks,
        codexPathOverride: codexPathOverride ?? null
    });

    const codexOptions: {
        apiKey?: string;
        baseUrl?: string;
        env: Record<string, string>;
        codexPathOverride?: string;
    } = {
        env: getProcessEnv()
    };

    if (apiKey && !useProjectConfig) {
        codexOptions.apiKey = apiKey;
    }
    if (baseUrl && !useProjectConfig) {
        codexOptions.baseUrl = baseUrl;
    }
    if (codexPathOverride) {
        codexOptions.codexPathOverride = codexPathOverride;
    }

    const codex = new Codex(codexOptions);
    const threadOptions: {
        workingDirectory?: string;
        skipGitRepoCheck?: boolean;
        model?: string;
        sandboxMode?: SandboxMode;
    } = {};

    if (workspace) {
        threadOptions.workingDirectory = workspace;
        threadOptions.skipGitRepoCheck = true;
    }
    if (model) {
        threadOptions.model = model;
    }
    threadOptions.sandboxMode = sandboxMode;

    const thread = threadId
        ? codex.resumeThread(threadId, threadOptions)
        : codex.startThread(threadOptions);
    const { events } = await withTimeout(thread.runStreamed(prompt), timeoutMs);
    const items = new Map<string, ThreadItem>();
    let finalResponse = "";
    let usage: unknown = null;
    const streamErrors: string[] = [];

    for await (const event of events) {
        const safeEvent = sanitizeEvent(event);
        writeStdout({
            type: "event",
            event: safeEvent
        });

        if (safeEvent.type === "item.started" || safeEvent.type === "item.updated" || safeEvent.type === "item.completed") {
            items.set(safeEvent.item.id, safeEvent.item);

            if (safeEvent.item.type === "agent_message") {
                finalResponse = safeEvent.item.text;
            }
        } else if (safeEvent.type === "turn.completed") {
            usage = safeEvent.usage;
        } else if (safeEvent.type === "turn.failed") {
            throw new Error(safeEvent.error.message);
        } else if (safeEvent.type === "error") {
            // The CLI may surface recoverable reconnect notices as `error` events
            // before it falls back to another transport, so keep streaming here.
            streamErrors.push(safeEvent.message);
        }
    }

    writeStdout({
        type: "completed",
        result: {
            finalResponse,
            items: Array.from(items.values()),
            usage,
            streamErrors
        }
    });
}

main().catch((err) => {
    writeStderr({
        type: "error",
        message: err?.message ?? String(err),
        stack: err?.stack
    });

    process.exit(1);
});
