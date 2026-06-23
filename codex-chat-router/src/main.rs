mod ccswitch;
mod provider;
mod proxy;

use axum::{
    Json, Router,
    body::Body,
    extract::State,
    http::{HeaderMap, HeaderValue, StatusCode, header},
    response::{IntoResponse, Response},
    routing::{get, post},
};
use ccswitch::providers::{
    codex_chat_history::{CodexChatHistoryStore, record_responses_sse_stream},
    streaming_codex_chat::create_responses_sse_stream_from_chat_with_context,
    transform_codex_chat::{
        build_codex_tool_context_from_request, chat_completion_to_response,
        responses_to_chat_completions,
    },
};
use clap::Parser;
use futures::StreamExt;
use proxy::error::ProxyError;
use reqwest::Client;
use serde_json::{Value, json};
use std::{io, net::SocketAddr, sync::Arc};
use tokio::net::TcpListener;
use tower_http::trace::TraceLayer;
use tracing::{info, warn};

#[derive(Debug, Parser)]
struct Args {
    #[arg(long, env = "LOOPAI_CODEX_PROXY_HOST", default_value = "127.0.0.1")]
    host: String,
    #[arg(long, env = "LOOPAI_CODEX_PROXY_PORT", default_value_t = 15721)]
    port: u16,
    #[arg(
        long,
        env = "LOOPAI_CODEX_PROXY_UPSTREAM_BASE_URL",
        default_value = "https://api.deepseek.com"
    )]
    upstream_base_url: String,
    #[arg(long, env = "LOOPAI_CODEX_PROXY_UPSTREAM_API_KEY")]
    upstream_api_key: Option<String>,
    #[arg(long, env = "DEEPSEEK_API_KEY")]
    deepseek_api_key: Option<String>,
    #[arg(long, env = "LOOPAI_CODEX_PROXY_MODEL", default_value = "deepseek-chat")]
    model: String,
}

#[derive(Clone)]
struct AppState {
    client: Client,
    upstream_v1: String,
    api_key: String,
    default_model: String,
    history: Arc<CodexChatHistoryStore>,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            std::env::var("RUST_LOG")
                .unwrap_or_else(|_| "codex_chat_router=info,tower_http=warn".to_string()),
        )
        .init();

    let args = Args::parse();
    let api_key = args
        .upstream_api_key
        .or(args.deepseek_api_key)
        .ok_or_else(|| {
            anyhow::anyhow!(
                "Missing LOOPAI_CODEX_PROXY_UPSTREAM_API_KEY or DEEPSEEK_API_KEY"
            )
        })?;

    let state = AppState {
        client: Client::new(),
        upstream_v1: normalize_upstream_v1(&args.upstream_base_url),
        api_key,
        default_model: args.model,
        history: Arc::new(CodexChatHistoryStore::default()),
    };

    let app = Router::new()
        .route("/health", get(health))
        .route("/v1/models", get(models))
        .route("/v1/responses", post(responses))
        .with_state(state.clone())
        .layer(TraceLayer::new_for_http());

    let addr: SocketAddr = format!("{}:{}", args.host, args.port).parse()?;
    let listener = TcpListener::bind(addr).await?;
    info!(
        local_base_url = %format!("http://{addr}/v1"),
        upstream_base_url = %state.upstream_v1,
        model = %state.default_model,
        "codex chat router started"
    );

    axum::serve(listener, app)
        .with_graceful_shutdown(shutdown_signal())
        .await?;

    Ok(())
}

async fn shutdown_signal() {
    let ctrl_c = async {
        if let Err(error) = tokio::signal::ctrl_c().await {
            warn!(%error, "failed to listen for ctrl-c");
        }
    };

    #[cfg(unix)]
    let terminate = async {
        match tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate()) {
            Ok(mut signal) => {
                signal.recv().await;
            }
            Err(error) => warn!(%error, "failed to listen for sigterm"),
        }
    };

    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();

    tokio::select! {
        _ = ctrl_c => {},
        _ = terminate => {},
    }
}

async fn health() -> impl IntoResponse {
    Json(json!({ "ok": true }))
}

async fn models(State(state): State<AppState>) -> Result<Response, ProxyError> {
    let upstream = format!("{}/models", state.upstream_v1);
    let response = state
        .client
        .get(upstream)
        .bearer_auth(&state.api_key)
        .send()
        .await
        .map_err(|error| ProxyError::UpstreamError(error.to_string()))?;

    let status = to_axum_status(response.status());
    let body = response
        .bytes()
        .await
        .map_err(|error| ProxyError::UpstreamError(error.to_string()))?;
    Ok((status, json_headers(), body).into_response())
}

async fn responses(
    State(state): State<AppState>,
    Json(mut body): Json<Value>,
) -> Result<Response, ProxyError> {
    state.history.enrich_request(&mut body).await;
    if body.get("model").is_none() {
        body["model"] = json!(state.default_model);
    }

    let tool_context = build_codex_tool_context_from_request(&body);
    let chat_body = responses_to_chat_completions(body)?;
    let stream = chat_body
        .get("stream")
        .and_then(|value| value.as_bool())
        .unwrap_or(false);

    let upstream = format!("{}/chat/completions", state.upstream_v1);
    let response = state
        .client
        .post(upstream)
        .bearer_auth(&state.api_key)
        .json(&chat_body)
        .send()
        .await
        .map_err(|error| ProxyError::UpstreamError(error.to_string()))?;

    let status = to_axum_status(response.status());
    if !status.is_success() {
        let body = response
            .bytes()
            .await
            .map_err(|error| ProxyError::UpstreamError(error.to_string()))?;
        return Ok((status, json_headers(), body).into_response());
    }

    if stream {
        let byte_stream = response
            .bytes_stream()
            .map(|result| result.map_err(|error| io::Error::other(error.to_string())));
        let responses_stream =
            create_responses_sse_stream_from_chat_with_context(byte_stream, tool_context);
        let recorded_stream = record_responses_sse_stream(responses_stream, state.history.clone());
        return Ok((
            sse_headers(),
            Body::from_stream(recorded_stream.map(|result| result.map_err(io::Error::other))),
        )
            .into_response());
    }

    let chat_response = response
        .json::<Value>()
        .await
        .map_err(|error| ProxyError::UpstreamError(error.to_string()))?;
    let responses_body = chat_completion_to_response(chat_response)?;
    state.history.record_response(&responses_body).await;
    Ok((StatusCode::OK, Json(responses_body)).into_response())
}

fn normalize_upstream_v1(base_url: &str) -> String {
    let trimmed = base_url.trim().trim_end_matches('/');
    if trimmed.ends_with("/v1") {
        trimmed.to_string()
    } else {
        format!("{trimmed}/v1")
    }
}

fn to_axum_status(status: reqwest::StatusCode) -> StatusCode {
    StatusCode::from_u16(status.as_u16()).unwrap_or(StatusCode::BAD_GATEWAY)
}

fn json_headers() -> HeaderMap {
    let mut headers = HeaderMap::new();
    headers.insert(
        header::CONTENT_TYPE,
        HeaderValue::from_static("application/json"),
    );
    headers
}

fn sse_headers() -> HeaderMap {
    let mut headers = HeaderMap::new();
    headers.insert(
        header::CONTENT_TYPE,
        HeaderValue::from_static("text/event-stream"),
    );
    headers.insert(header::CACHE_CONTROL, HeaderValue::from_static("no-cache"));
    headers.insert(header::CONNECTION, HeaderValue::from_static("keep-alive"));
    headers
}
