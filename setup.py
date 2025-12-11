from setuptools import setup, find_packages

setup(
    name="loopai",
    version="0.0.1",
    packages=find_packages(),
    install_requires=[
        "langgraph>=0.6.7",
        "colorlog>=6.10.0",
        "rich>=13.0.0",
        "langchain>=0.3.27",
        "langchain-community>=0.3.0",
        "langchain-openai>=0.1.0",
        "langchain-core>=0.3.0",
        "langchain-text-splitters>=0.2.0",
        "omegaconf>=2.3.0",
        "httpx>=0.24.0",
        "chromadb>=0.4.0",
        "ddgs",
        # Download dependencies
        "huggingface_hub>=0.20.0",
        "datasets>=2.14.0",
        "kaggle>=1.5.0",
        "kagglehub>=0.2.0",
        "playwright>=1.40.0",
        "tenacity>=8.2.0",
        "requests>=2.31.0",
        "mcp>=0.1.0",
        "aiosqlite>=0.21.0"
    ],
    python_requires=">=3.12",
)
