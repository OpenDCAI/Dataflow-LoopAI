import { startChatResponsesProxy } from "./chatResponsesProxy.js";

const upstreamBaseUrl = process.env.CODEX_UPSTREAM_BASE_URL
    ?? process.env.CODEX_BASE_URL
    ?? process.env.DEEPSEEK_BASE_URL
    ?? "https://api.deepseek.com";
const apiKey = process.env.CODEX_UPSTREAM_API_KEY
    ?? process.env.CODEX_API_KEY
    ?? process.env.DEEPSEEK_API_KEY;
const defaultModel = process.env.CODEX_MODEL ?? process.env.DEEPSEEK_MODEL ?? "deepseek-chat";
const host = process.env.CODEX_LOCAL_PROXY_HOST ?? "127.0.0.1";
const port = Number(process.env.CODEX_LOCAL_PROXY_PORT ?? "15721");

if (!apiKey) {
    console.error("Missing CODEX_API_KEY or DEEPSEEK_API_KEY.");
    process.exit(1);
}

const proxy = await startChatResponsesProxy({
    upstreamBaseUrl,
    apiKey,
    defaultModel,
    host,
    port
});

console.log(JSON.stringify({
    type: "codex.chat_proxy.started",
    baseUrl: proxy.baseUrl,
    upstreamBaseUrl,
    model: defaultModel
}));

const shutdown = async () => {
    await proxy.close();
    process.exit(0);
};

process.on("SIGINT", () => {
    void shutdown();
});
process.on("SIGTERM", () => {
    void shutdown();
});
