import { Codex, type ThreadEvent, type ThreadItem } from "@openai/codex-sdk";
import { shouldUseChatResponsesProxy, startChatResponsesProxy } from "./chatResponsesProxy.js";

const prompt = process.argv.slice(2).join(" ");
const timeoutMs = Number(process.env.CODEX_RUN_TIMEOUT_MS ?? "300000");
const workspace = process.env.CODEX_WORKSPACE;
const model = process.env.CODEX_MODEL ?? process.env.DEEPSEEK_MODEL;
const baseUrl = process.env.CODEX_BASE_URL ?? process.env.DEEPSEEK_BASE_URL;
const apiKey = process.env.CODEX_API_KEY ?? process.env.DEEPSEEK_API_KEY;
const codexHome = process.env.CODEX_HOME;
const threadId = process.env.CODEX_THREAD_ID?.trim() || "";
const useProjectConfig = process.env.CODEX_USE_PROJECT_CONFIG === "1";
const rustRouterEnabled = process.env.CODEX_USE_RUST_CHAT_ROUTER !== "0";
const externalRouterBaseUrl = (process.env.CODEX_CHAT_ROUTER_BASE_URL
    ?? `http://${process.env.CODEX_LOCAL_PROXY_HOST ?? "127.0.0.1"}:${process.env.CODEX_LOCAL_PROXY_PORT ?? "15721"}/v1`).replace(/\/$/, "");

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

function routerHealthUrl(baseUrl: string): string {
    return baseUrl.replace(/\/v1$/, "") + "/health";
}

async function isExternalRouterReady(baseUrl: string): Promise<boolean> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 500);

    try {
        const response = await fetch(routerHealthUrl(baseUrl), {
            signal: controller.signal
        });
        return response.ok;
    } catch {
        return false;
    } finally {
        clearTimeout(timer);
    }
}

async function main() {
    const useLocalChatRouting = Boolean(baseUrl && !useProjectConfig && shouldUseChatResponsesProxy(baseUrl));
    const useExternalRouter = useLocalChatRouting
        && rustRouterEnabled
        && await isExternalRouterReady(externalRouterBaseUrl);
    const localProxy = useLocalChatRouting && baseUrl && !useExternalRouter
        ? await startChatResponsesProxy({
            upstreamBaseUrl: baseUrl,
            apiKey,
            defaultModel: model
        })
        : null;
    const effectiveBaseUrl = useExternalRouter ? externalRouterBaseUrl : (localProxy?.baseUrl ?? baseUrl);

    writeStderr({
        type: "runner.started",
        cwd: process.cwd(),
        prompt,
        timeoutMs,
        workspace,
        model,
        baseUrl: effectiveBaseUrl,
        upstreamBaseUrl: (localProxy || useExternalRouter) ? baseUrl : undefined,
        localChatRouting: Boolean(localProxy || useExternalRouter),
        localChatRoutingKind: useExternalRouter ? "rust-router" : (localProxy ? "typescript-fallback" : null),
        apiKeySource: apiKey ? "env" : "default",
        codexHome,
        threadId: threadId || null,
        useProjectConfig
    });

    const codexOptions: {
        apiKey?: string;
        baseUrl?: string;
        config?: {
            model_provider?: string;
            model_providers?: Record<string, {
                name: string;
                base_url: string;
                env_key: string;
                wire_api: string;
                supports_websockets: boolean;
            }>;
        };
        env: Record<string, string>;
    } = {
        env: getProcessEnv()
    };

    if (apiKey && !useProjectConfig) {
        codexOptions.apiKey = apiKey;
    }
    if (localProxy || useExternalRouter) {
        const providerName = "loopai_local_chat_proxy";
        codexOptions.config = {
            model_provider: providerName,
            model_providers: {
                [providerName]: {
                    name: "LoopAI Local Chat Proxy",
                    base_url: effectiveBaseUrl ?? externalRouterBaseUrl,
                    env_key: "CODEX_API_KEY",
                    wire_api: "responses",
                    supports_websockets: false
                }
            }
        };
    } else if (effectiveBaseUrl && !useProjectConfig) {
        codexOptions.baseUrl = effectiveBaseUrl;
    }

    try {
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
    } finally {
        if (localProxy) {
            await localProxy.close();
        }
    }
}

main().catch((err) => {
    writeStderr({
        type: "error",
        message: err?.message ?? String(err),
        stack: err?.stack
    });

    process.exit(1);
});
