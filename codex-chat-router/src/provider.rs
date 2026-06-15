#[derive(Debug, Clone, Default)]
pub struct CodexChatReasoningConfig {
    pub supports_effort: Option<bool>,
    pub effort_param: Option<String>,
    pub effort_value_mode: Option<String>,
    pub supports_thinking: Option<bool>,
    pub thinking_param: Option<String>,
    pub enabled_value: Option<serde_json::Value>,
    pub disabled_value: Option<serde_json::Value>,
}
