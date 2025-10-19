# %%
# pip install -qU "langchain[anthropic]" to call the model
from langgraph.prebuilt import create_react_agent
from langchain_openai import ChatOpenAI

def get_weather(city: str) -> str:
    """Get weather for a given city."""
    return f"It's always sunny in {city}!"

vllm_model = ChatOpenAI(
    base_url="http://localhost:8911/v1",  # vLLM 默认接口
    api_key="EMPTY",                      # 随便填，不校验
    model="/data1/lianghao/lpc/models/Qwen3-32B/"      # 与启动时的模型名保持一致
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
