use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use serde_json::json;

#[derive(Debug, thiserror::Error)]
pub enum ProxyError {
    #[error("transform error: {0}")]
    TransformError(String),
    #[error("upstream error: {0}")]
    UpstreamError(String),
}

impl IntoResponse for ProxyError {
    fn into_response(self) -> Response {
        let status = match self {
            ProxyError::TransformError(_) => StatusCode::BAD_REQUEST,
            ProxyError::UpstreamError(_) => StatusCode::BAD_GATEWAY,
        };
        let message = self.to_string();
        (
            status,
            axum::Json(json!({
                "error": {
                    "message": message,
                    "type": "codex_chat_router_error"
                }
            })),
        )
            .into_response()
    }
}
