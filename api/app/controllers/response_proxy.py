import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from ..services.responseProxy import ResponseProxyService


router = APIRouter(tags=["responseProxy"])


@router.get("/health", operation_id="responseProxyHealth", summary="Response proxy health")
async def response_proxy_health():
    return {"ok": True, "service": "responseProxy"}


@router.get("/v1/models", operation_id="responseProxyModels", summary="Proxy upstream models")
async def response_proxy_models():
    service = await ResponseProxyService.create()
    status_code, content = await service.list_models()
    return Response(content=content, status_code=status_code, media_type="application/json")


@router.post("/v1/responses", operation_id="responseProxyResponses", summary="Bridge responses API to chat completions")
async def response_proxy_responses(request: Request):
    body = await request.json()
    if not isinstance(body, dict):
        return JSONResponse(
            status_code=400,
            content={"error": {"message": "request body must be a JSON object", "type": "invalid_request_error"}},
        )

    service = await ResponseProxyService.create()
    mode, payload, status_code = await service.forward_responses_request(body)
    if mode == "error":
        try:
            content = json.loads(payload.decode("utf-8"))
        except Exception:
            content = {"error": {"message": payload.decode("utf-8", errors="ignore") or "upstream error"}}
        return JSONResponse(status_code=status_code, content=content)
    if mode == "json":
        return JSONResponse(status_code=status_code, content=payload)
    return StreamingResponse(
        payload,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
