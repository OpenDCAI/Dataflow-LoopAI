import { Codex, type ThreadEvent, type ThreadItem } from "@openai/codex-sdk";

const prompt = process.argv.slice(2).join(" ");
const timeoutMs = Number(process.env.CODEX_RUN_TIMEOUT_MS ?? "300000");
const workspace = process.env.CODEX_WORKSPACE;
const model = process.env.CODEX_MODEL ?? process.env.DEEPSEEK_MODEL;
const baseUrl = process.env.CODEX_BASE_URL ?? process.env.DEEPSEEK_BASE_URL;
const apiKey = process.env.CODEX_API_KEY ?? process.env.DEEPSEEK_API_KEY;
const codexHome = process.env.CODEX_HOME;
const threadId = process.env.CODEX_THREAD_ID?.trim() || "";
const useProjectConfig = process.env.CODEX_USE_PROJECT_CONFIG === "1";
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

async function main() {
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
        threadId: threadId || null,
        useProjectConfig
    });

    const codexOptions: {
        apiKey?: string;
        baseUrl?: string;
        env: Record<string, string>;
    } = {
        env: getProcessEnv()
    };

    if (apiKey && !useProjectConfig) {
        codexOptions.apiKey = apiKey;
    }
    if (baseUrl && !useProjectConfig) {
        codexOptions.baseUrl = baseUrl;
    }

    const codex = new Codex(codexOptions);
    const threadOptions: {
        workingDirectory?: string;
        skipGitRepoCheck?: boolean;
        model?: string;
    } = {};

    if (workspace) {
        threadOptions.workingDirectory = workspace;
        threadOptions.skipGitRepoCheck = true;
    }
    if (model) {
        threadOptions.model = model;
    }

    const thread = threadId
        ? codex.resumeThread(threadId, threadOptions)
        : codex.startThread(threadOptions);
    const { events } = await withTimeout(thread.runStreamed(prompt), timeoutMs);
    const items = new Map<string, ThreadItem>();
    let finalResponse = "";
    let usage: unknown = null;
    const streamErrors: string[] = [];

    for await (const event of events) {
        writeStdout({
            type: "event",
            event
        });

        if (event.type === "item.started" || event.type === "item.updated" || event.type === "item.completed") {
            items.set(event.item.id, event.item);

            if (event.item.type === "agent_message") {
                finalResponse = event.item.text;
            }
        } else if (event.type === "turn.completed") {
            usage = event.usage;
        } else if (event.type === "turn.failed") {
            throw new Error(event.error.message);
        } else if (event.type === "error") {
            // The CLI may surface recoverable reconnect notices as `error` events
            // before it falls back to another transport, so keep streaming here.
            streamErrors.push(event.message);
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
