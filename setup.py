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
        "omegaconf>=2.3.0",
    ],
    python_requires=">=3.12",
)
