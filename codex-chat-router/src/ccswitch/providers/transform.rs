use serde_json::{Value, json};

pub(crate) fn is_openai_o_series(model: &str) -> bool {
    let model = model.to_ascii_lowercase();
    model.starts_with("o1")
        || model.starts_with("o3")
        || model.starts_with("o4")
        || model.starts_with("gpt-5")
}

pub(crate) fn supports_reasoning_effort(model: &str) -> bool {
    is_openai_o_series(model)
}

pub(crate) fn inject_openai_stream_include_usage(body: &mut Value) {
    if body.get("stream").and_then(|value| value.as_bool()) != Some(true) {
        return;
    }

    let Some(object) = body.as_object_mut() else {
        return;
    };

    match object.get_mut("stream_options") {
        Some(Value::Object(stream_options)) => {
            stream_options.insert("include_usage".to_string(), json!(true));
        }
        _ => {
            object.insert(
                "stream_options".to_string(),
                json!({ "include_usage": true }),
            );
        }
    }
}
