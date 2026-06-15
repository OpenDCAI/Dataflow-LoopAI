import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { randomUUID } from "node:crypto";

type JsonObject = Record<string, unknown>;

type ProxyOptions = {
    upstreamBaseUrl: string;
    apiKey?: string;
    defaultModel?: string;
    host?: string;
    port?: number;
};

type StartedProxy = {
    baseUrl: string;
    close: () => Promise<void>;
};

const encoder = new TextEncoder();

export function shouldUseChatResponsesProxy(baseUrl?: string): boolean {
    const forced = process.env.CODEX_LOCAL_ROUTING ?? process.env.CODEX_USE_CHAT_RESPONSES_PROXY;
    if (forced) {
        return ["1", "true", "yes", "on"].includes(forced.toLowerCase());
    }

    const apiFormat = process.env.CODEX_API_FORMAT ?? process.env.CODEX_WIRE_API;
    if (apiFormat) {
        return ["openai_chat", "chat", "chat_completions", "chat-completions"].includes(apiFormat.toLowerCase());
    }

    return Boolean(baseUrl && /(^|\.)deepseek\.com\/?/i.test(baseUrl));
}

export async function startChatResponsesProxy(options: ProxyOptions): Promise<StartedProxy> {
    const server = createServer((req, res) => {
        handleRequest(req, res, options).catch((error: unknown) => {
            writeJson(res, 500, {
                error: {
                    message: error instanceof Error ? error.message : String(error),
                    type: "local_routing_error"
                }
            });
        });
    });

    await new Promise<void>((resolve, reject) => {
        server.once("error", reject);
        server.listen(options.port ?? 0, options.host ?? "127.0.0.1", () => {
            server.off("error", reject);
            resolve();
        });
    });

    const address = server.address();
    if (!address || typeof address === "string") {
        throw new Error("Failed to start local Codex chat routing proxy");
    }

    const host = options.host ?? "127.0.0.1";
    return {
        baseUrl: `http://${host}:${address.port}/v1`,
        close: () => closeServer(server)
    };
}

async function closeServer(server: Server): Promise<void> {
    await new Promise<void>((resolve, reject) => {
        server.close((error) => {
            if (error) {
                reject(error);
            } else {
                resolve();
            }
        });
    });
}

async function handleRequest(req: IncomingMessage, res: ServerResponse, options: ProxyOptions): Promise<void> {
    const method = req.method ?? "GET";
    const url = new URL(req.url ?? "/", "http://127.0.0.1");
    const path = url.pathname.replace(/^\/v1/, "");

    if (method === "GET" && path === "/models") {
        const model = options.defaultModel || "deepseek-chat";
        writeJson(res, 200, {
            object: "list",
            data: [{ id: model, object: "model", created: 0, owned_by: "local-routing" }]
        });
        return;
    }

    if (method !== "POST" || path !== "/responses") {
        writeJson(res, 404, {
            error: {
                message: `Unsupported local routing endpoint: ${method} ${url.pathname}`,
                type: "not_found"
            }
        });
        return;
    }

    const body = await readJsonBody(req);
    const chatBody = responsesRequestToChatCompletions(body, options.defaultModel);
    const upstreamUrl = chatCompletionsUrl(options.upstreamBaseUrl);
    const upstreamResponse = await fetch(upstreamUrl, {
        method: "POST",
        headers: {
            "content-type": "application/json",
            accept: chatBody.stream ? "text/event-stream" : "application/json",
            ...(options.apiKey ? { authorization: `Bearer ${options.apiKey}` } : {})
        },
        body: JSON.stringify(chatBody)
    });

    if (!upstreamResponse.ok) {
        await forwardUpstreamError(upstreamResponse, res);
        return;
    }

    if (chatBody.stream) {
        await streamChatAsResponses(upstreamResponse, res, chatBody.model);
    } else {
        const upstreamJson = (await upstreamResponse.json()) as JsonObject;
        writeJson(res, 200, chatCompletionToResponse(upstreamJson, chatBody.model));
    }
}

async function readJsonBody(req: IncomingMessage): Promise<JsonObject> {
    const chunks: Buffer[] = [];
    for await (const chunk of req) {
        chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
    }

    const text = Buffer.concat(chunks).toString("utf-8").trim();
    if (!text) {
        return {};
    }

    const parsed = JSON.parse(text) as unknown;
    if (!isObject(parsed)) {
        throw new Error("Expected JSON object request body");
    }
    return parsed;
}

function responsesRequestToChatCompletions(body: JsonObject, defaultModel?: string): JsonObject {
    const messages = responsesInputToMessages(body);
    const model = stringValue(body.model) || defaultModel || "deepseek-chat";
    const result: JsonObject = {
        model,
        messages,
        stream: body.stream === true
    };

    copyIfPresent(body, result, "temperature");
    copyIfPresent(body, result, "top_p");
    copyIfPresent(body, result, "presence_penalty");
    copyIfPresent(body, result, "frequency_penalty");
    copyMapped(body, result, "max_output_tokens", "max_tokens");
    copyTools(body, result);

    return result;
}

function responsesInputToMessages(body: JsonObject): JsonObject[] {
    const messages: JsonObject[] = [];
    const instructions = stringValue(body.instructions);
    if (instructions) {
        messages.push({ role: "system", content: instructions });
    }

    const input = body.input;
    if (typeof input === "string") {
        messages.push({ role: "user", content: input });
    } else if (Array.isArray(input)) {
        let pendingToolCalls: JsonObject[] = [];
        const flushPendingToolCalls = () => {
            if (pendingToolCalls.length === 0) {
                return;
            }
            messages.push({
                role: "assistant",
                content: "",
                tool_calls: pendingToolCalls,
                reasoning_content: "tool call"
            });
            pendingToolCalls = [];
        };

        for (const item of input) {
            if (isObject(item) && stringValue(item.type) === "function_call") {
                pendingToolCalls.push(responseFunctionCallToChatToolCall(item));
                continue;
            }

            flushPendingToolCalls();
            const converted = responseInputItemToMessage(item);
            if (converted) {
                messages.push(converted);
            }
        }
        flushPendingToolCalls();
    }

    if (messages.length === 0) {
        messages.push({ role: "user", content: "" });
    }
    return messages;
}

function responseInputItemToMessage(item: unknown): JsonObject | null {
    if (!isObject(item)) {
        return null;
    }

    const type = stringValue(item.type);
    if (type === "function_call_output") {
        return {
            role: "tool",
            tool_call_id: stringValue(item.call_id) || stringValue(item.id) || `call_${randomUUID()}`,
            content: stringifyContent(item.output)
        };
    }

    if (type === "function_call") {
        return {
            role: "assistant",
            content: "",
            tool_calls: [responseFunctionCallToChatToolCall(item)]
        };
    }

    const role = normalizeRole(stringValue(item.role));
    if (!role) {
        return null;
    }

    const message: JsonObject = {
        role,
        content: contentPartsToText(item.content)
    };

    if (role === "assistant" && Array.isArray(item.tool_calls)) {
        message.tool_calls = item.tool_calls;
        if (!stringValue(message.content)) {
            message.content = "";
        }
    }

    return message;
}

function responseFunctionCallToChatToolCall(item: JsonObject): JsonObject {
    return {
        id: stringValue(item.call_id) || stringValue(item.id) || `call_${randomUUID()}`,
        type: "function",
        function: {
            name: stringValue(item.name) || "unknown",
            arguments: stringifyContent(item.arguments)
        }
    };
}

function copyTools(source: JsonObject, target: JsonObject): void {
    if (!Array.isArray(source.tools)) {
        return;
    }

    const tools = source.tools
        .map((tool) => {
            if (!isObject(tool)) {
                return null;
            }
            if (tool.type === "function") {
                return {
                    type: "function",
                    function: {
                        name: tool.name,
                        description: tool.description,
                        parameters: tool.parameters ?? {}
                    }
                };
            }
            return null;
        })
        .filter(Boolean);

    if (tools.length > 0) {
        target.tools = tools;
        copyIfPresent(source, target, "tool_choice");
        copyIfPresent(source, target, "parallel_tool_calls");
    }
}

async function forwardUpstreamError(upstreamResponse: Response, res: ServerResponse): Promise<void> {
    const contentType = upstreamResponse.headers.get("content-type") ?? "application/json";
    const text = await upstreamResponse.text();
    res.writeHead(upstreamResponse.status, { "content-type": contentType });
    res.end(text);
}

async function streamChatAsResponses(upstreamResponse: Response, res: ServerResponse, model: unknown): Promise<void> {
    res.writeHead(200, {
        "content-type": "text/event-stream; charset=utf-8",
        "cache-control": "no-cache, no-transform",
        connection: "keep-alive"
    });

    const responseId = `resp_${randomUUID().replaceAll("-", "")}`;
    const messageId = `msg_${randomUUID().replaceAll("-", "")}`;
    const createdAt = Math.floor(Date.now() / 1000);
    const textParts: string[] = [];
    const reasoningParts: string[] = [];
    const toolCalls = new Map<number, { id: string; name: string; arguments: string; itemId: string; started: boolean; outputIndex: number }>();
    let messageStarted = false;
    let contentPartStarted = false;
    let messageOutputIndex = -1;
    let nextOutputIndex = 0;

    const emit = (event: string, data: JsonObject) => writeSse(res, event, data);
    emit("response.created", {
        type: "response.created",
        response: baseResponse(responseId, model, createdAt, "in_progress", [])
    });

    const reader = upstreamResponse.body?.getReader();
    if (!reader) {
        throw new Error("Upstream streaming response has no body");
    }

    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
        const { value, done } = await reader.read();
        if (done) {
            break;
        }

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split(/\r?\n/);
        buffer = lines.pop() ?? "";

        for (const line of lines) {
            if (!line.startsWith("data:")) {
                continue;
            }

            const data = line.slice(5).trim();
            if (!data || data === "[DONE]") {
                continue;
            }

            const chunk = JSON.parse(data) as JsonObject;
            const delta = firstChoiceDelta(chunk);
            if (!delta) {
                continue;
            }

            const reasoning = stringValue(delta.reasoning_content);
            if (reasoning) {
                reasoningParts.push(reasoning);
            }

            const content = stringValue(delta.content);
            if (content) {
                if (!messageStarted) {
                    messageStarted = true;
                    messageOutputIndex = nextOutputIndex;
                    nextOutputIndex += 1;
                    emit("response.output_item.added", {
                        type: "response.output_item.added",
                        output_index: messageOutputIndex,
                        item: { id: messageId, type: "message", status: "in_progress", role: "assistant", content: [] }
                    });
                }
                if (!contentPartStarted) {
                    contentPartStarted = true;
                    emit("response.content_part.added", {
                        type: "response.content_part.added",
                        item_id: messageId,
                        output_index: messageOutputIndex,
                        content_index: 0,
                        part: { type: "output_text", text: "" }
                    });
                }
                textParts.push(content);
                emit("response.output_text.delta", {
                    type: "response.output_text.delta",
                    item_id: messageId,
                    output_index: messageOutputIndex,
                    content_index: 0,
                    delta: content
                });
            }

            const deltaToolCalls = Array.isArray(delta.tool_calls) ? delta.tool_calls : [];
            for (const rawToolCall of deltaToolCalls) {
                if (!isObject(rawToolCall)) {
                    continue;
                }
                const index = Number(rawToolCall.index ?? 0);
                const tool = toolCalls.get(index) ?? {
                    id: stringValue(rawToolCall.id) || `call_${randomUUID().replaceAll("-", "")}`,
                    name: "",
                    arguments: "",
                    itemId: `fc_${randomUUID().replaceAll("-", "")}`,
                    started: false,
                    outputIndex: nextOutputIndex
                };
                const fn = isObject(rawToolCall.function) ? rawToolCall.function : {};
                tool.name += stringValue(fn.name);
                tool.arguments += stringValue(fn.arguments);

                if (!tool.started) {
                    tool.started = true;
                    nextOutputIndex = Math.max(nextOutputIndex, tool.outputIndex + 1);
                    toolCalls.set(index, tool);
                    emit("response.output_item.added", {
                        type: "response.output_item.added",
                        output_index: tool.outputIndex,
                        item: {
                            id: tool.itemId,
                            type: "function_call",
                            status: "in_progress",
                            call_id: tool.id,
                            name: tool.name,
                            arguments: ""
                        }
                    });
                } else {
                    toolCalls.set(index, tool);
                }

                const argumentDelta = stringValue(fn.arguments);
                if (argumentDelta) {
                    emit("response.function_call_arguments.delta", {
                        type: "response.function_call_arguments.delta",
                        item_id: tool.itemId,
                        output_index: tool.outputIndex,
                        delta: argumentDelta
                    });
                }
            }
        }
    }

    const output: JsonObject[] = [];
    if (messageStarted) {
        const finalText = textParts.join("");
        emit("response.output_text.done", {
            type: "response.output_text.done",
            item_id: messageId,
            output_index: messageOutputIndex,
            content_index: 0,
            text: finalText
        });
        emit("response.content_part.done", {
            type: "response.content_part.done",
            item_id: messageId,
            output_index: messageOutputIndex,
            content_index: 0,
            part: { type: "output_text", text: finalText }
        });
        const messageItem = {
            id: messageId,
            type: "message",
            status: "completed",
            role: "assistant",
            content: [{ type: "output_text", text: finalText }]
        };
        output.push(messageItem);
        emit("response.output_item.done", {
            type: "response.output_item.done",
            output_index: messageOutputIndex,
            item: messageItem
        });
    }

    for (const tool of [...toolCalls.values()].sort((a, b) => a.outputIndex - b.outputIndex)) {
        const item = {
            id: tool.itemId,
            type: "function_call",
            status: "completed",
            call_id: tool.id,
            name: tool.name,
            arguments: tool.arguments
        };
        emit("response.function_call_arguments.done", {
            type: "response.function_call_arguments.done",
            item_id: tool.itemId,
            output_index: tool.outputIndex,
            arguments: tool.arguments
        });
        emit("response.output_item.done", {
            type: "response.output_item.done",
            output_index: tool.outputIndex,
            item
        });
        output.push(item);
    }

    const response = baseResponse(responseId, model, createdAt, "completed", output);
    if (reasoningParts.length > 0) {
        response.reasoning = { summary: [{ type: "summary_text", text: reasoningParts.join("") }] };
    }
    emit("response.completed", {
        type: "response.completed",
        response
    });
    writeSseDone(res);
    res.end();
}

function chatCompletionToResponse(upstreamJson: JsonObject, fallbackModel: unknown): JsonObject {
    const responseId = stringValue(upstreamJson.id) || `resp_${randomUUID().replaceAll("-", "")}`;
    const createdAt = Number(upstreamJson.created ?? Math.floor(Date.now() / 1000));
    const model = upstreamJson.model ?? fallbackModel;
    const choice = Array.isArray(upstreamJson.choices) && isObject(upstreamJson.choices[0]) ? upstreamJson.choices[0] : {};
    const message = isObject(choice.message) ? choice.message : {};
    const output: JsonObject[] = [];
    const content = stringValue(message.content);

    if (content) {
        output.push({
            id: `msg_${randomUUID().replaceAll("-", "")}`,
            type: "message",
            status: "completed",
            role: "assistant",
            content: [{ type: "output_text", text: content }]
        });
    }

    if (Array.isArray(message.tool_calls)) {
        for (const toolCall of message.tool_calls) {
            if (!isObject(toolCall)) {
                continue;
            }
            const fn = isObject(toolCall.function) ? toolCall.function : {};
            output.push({
                id: `fc_${randomUUID().replaceAll("-", "")}`,
                type: "function_call",
                status: "completed",
                call_id: stringValue(toolCall.id) || `call_${randomUUID().replaceAll("-", "")}`,
                name: stringValue(fn.name),
                arguments: stringValue(fn.arguments)
            });
        }
    }

    const response = baseResponse(responseId, model, createdAt, "completed", output);
    if (upstreamJson.usage) {
        response.usage = upstreamJson.usage;
    }
    const reasoning = stringValue(message.reasoning_content);
    if (reasoning) {
        response.reasoning = { summary: [{ type: "summary_text", text: reasoning }] };
    }
    return response;
}

function baseResponse(id: string, model: unknown, createdAt: number, status: string, output: JsonObject[]): JsonObject {
    return {
        id,
        object: "response",
        created_at: createdAt,
        status,
        error: null,
        incomplete_details: null,
        instructions: null,
        max_output_tokens: null,
        model: stringValue(model) || "deepseek-chat",
        output,
        parallel_tool_calls: true,
        previous_response_id: null,
        reasoning: null,
        store: false,
        temperature: null,
        text: { format: { type: "text" } },
        tool_choice: "auto",
        tools: [],
        top_p: null,
        truncation: "disabled",
        usage: null,
        user: null,
        metadata: {}
    };
}

function firstChoiceDelta(chunk: JsonObject): JsonObject | null {
    if (!Array.isArray(chunk.choices) || !isObject(chunk.choices[0])) {
        return null;
    }

    const delta = chunk.choices[0].delta;
    return isObject(delta) ? delta : null;
}

function contentPartsToText(content: unknown): string {
    if (typeof content === "string") {
        return content;
    }
    if (!Array.isArray(content)) {
        return stringifyContent(content);
    }

    return content
        .map((part) => {
            if (typeof part === "string") {
                return part;
            }
            if (!isObject(part)) {
                return "";
            }
            return stringValue(part.text) || stringValue(part.output_text) || stringValue(part.input_text);
        })
        .join("");
}

function stringifyContent(value: unknown): string {
    if (typeof value === "string") {
        return value;
    }
    if (value == null) {
        return "";
    }
    return JSON.stringify(value);
}

function normalizeRole(role: string): string | null {
    if (role === "developer") {
        return "system";
    }
    if (["system", "user", "assistant", "tool"].includes(role)) {
        return role;
    }
    return null;
}

function chatCompletionsUrl(baseUrl: string): string {
    const normalized = baseUrl.replace(/\/+$/, "");
    if (/\/chat\/completions$/i.test(normalized)) {
        return normalized;
    }
    if (/\/v1$/i.test(normalized)) {
        return `${normalized}/chat/completions`;
    }
    return `${normalized}/chat/completions`;
}

function writeJson(res: ServerResponse, status: number, payload: unknown): void {
    res.writeHead(status, { "content-type": "application/json; charset=utf-8" });
    res.end(JSON.stringify(payload));
}

function writeSse(res: ServerResponse, event: string, payload: JsonObject): void {
    res.write(`event: ${event}\n`);
    res.write(`data: ${JSON.stringify(payload)}\n\n`);
}

function writeSseDone(res: ServerResponse): void {
    res.write("data: [DONE]\n\n");
}

function copyIfPresent(source: JsonObject, target: JsonObject, key: string): void {
    if (source[key] !== undefined && source[key] !== null) {
        target[key] = source[key];
    }
}

function copyMapped(source: JsonObject, target: JsonObject, sourceKey: string, targetKey: string): void {
    if (source[sourceKey] !== undefined && source[sourceKey] !== null) {
        target[targetKey] = source[sourceKey];
    }
}

function stringValue(value: unknown): string {
    return typeof value === "string" ? value : "";
}

function isObject(value: unknown): value is JsonObject {
    return typeof value === "object" && value !== null && !Array.isArray(value);
}
