from __future__ import annotations

import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from api.app.services.responseProxy.proxy import ResponseProxyService
from loopai.utils.model_pool import StarterModelPool


class _LLMHandler(BaseHTTPRequestHandler):
    calls: list[dict] = []

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("content-length") or "0")
        body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        self.__class__.calls.append({
            "path": self.path,
            "body": body,
            "authorization": self.headers.get("authorization"),
        })
        if self.path.endswith("/chat/completions"):
            payload = {
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 1,
                "model": body.get("model"),
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "pong"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            }
        elif self.path.endswith("/responses"):
            payload = {
                "id": "resp_test",
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "model": body.get("model"),
                "output": [
                    {
                        "id": "msg_test",
                        "type": "message",
                        "status": "completed",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "pong", "annotations": []}],
                    }
                ],
                "usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
            }
        else:
            self.send_response(404)
            self.end_headers()
            return
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args):
        return


def _start_server() -> tuple[HTTPServer, str]:
    _LLMHandler.calls = []
    server = HTTPServer(("127.0.0.1", 0), _LLMHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}/v1"


def _system(base_url: str, wire_api: str) -> dict:
    return {
        "api_port": 8855,
        "model": {
            "proxy_base_url": "http://127.0.0.1:8855/responseProxy/v1",
            "proxy_api_key": "loopai-local-proxy",
            "default_tier": "medium",
            "pool": [
                {
                    "tier": "medium",
                    "name": "medium",
                    "model_name": "upstream-model",
                    "base_url": base_url,
                    "api_key": "upstream-key",
                    "maxworker": 2,
                    "wire_api": wire_api,
                }
            ],
        },
    }


def test_starter_model_pool_resolves_proxy_provider():
    pool = StarterModelPool(_system("http://upstream.example/v1", "chat"))
    provider = pool.resolve_proxy_provider("medium")

    assert provider is not None
    assert provider.base_url == "http://127.0.0.1:8855/responseProxy/v1"
    assert provider.api_key == "loopai-local-proxy"
    assert provider.model == "medium"
    assert provider.upstream_model_name == "upstream-model"


def test_responses_endpoint_routes_to_chat_upstream_through_pool():
    server, base_url = _start_server()
    try:
        service = ResponseProxyService(_system(base_url, "chat"))
        mode, payload, status = asyncio.run(
            service.forward_responses_request(
                {
                    "model": "medium",
                    "input": "ping",
                    "max_output_tokens": 4,
                    "stream": False,
                }
            )
        )
    finally:
        server.shutdown()

    assert status == 200
    assert mode == "json"
    assert payload["output"][0]["content"][0]["text"] == "pong"
    assert _LLMHandler.calls[-1]["path"] == "/v1/chat/completions"
    assert _LLMHandler.calls[-1]["body"]["model"] == "upstream-model"
    assert _LLMHandler.calls[-1]["authorization"] == "Bearer upstream-key"


def test_chat_endpoint_routes_to_responses_upstream_through_pool():
    server, base_url = _start_server()
    try:
        service = ResponseProxyService(_system(base_url, "responses"))
        mode, payload, status = asyncio.run(
            service.forward_chat_request(
                {
                    "model": "medium",
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 4,
                    "stream": False,
                }
            )
        )
    finally:
        server.shutdown()

    assert status == 200
    assert mode == "json"
    assert payload["choices"][0]["message"]["content"] == "pong"
    assert _LLMHandler.calls[-1]["path"] == "/v1/responses"
    assert _LLMHandler.calls[-1]["body"]["model"] == "upstream-model"
