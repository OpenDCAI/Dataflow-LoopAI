# %%
# pip install -qU "langchain[anthropic]" to call the model
from langgraph.prebuilt import create_react_agent
from langchain_openai import ChatOpenAI
from loopai.schema.model_pool import StarterModelPool, load_starter_system_config_sync

def get_weather(city: str) -> str:
    """Get weather for a given city."""
    return f"It's always sunny in {city}!"

provider = StarterModelPool(
    load_starter_system_config_sync(prefer_db=True) or {}
).resolve_role_provider("codex")
if provider is None:
    raise RuntimeError("Configure a Starter model-pool Codex provider first")
vllm_model = ChatOpenAI(
    base_url=provider.base_url,
    api_key=provider.api_key,
    model=provider.model,
)

agent = create_react_agent(
    model=vllm_model,
    tools=[get_weather],
    prompt="You are a helpful assistant"
)

# %%
# Run the agent
agent.invoke(
    {"messages": [{"role": "user", "content": "what is the weather in sf"}]}
)['messages']

# %%
