"""
Web search tool for the Postprocess dataset agent.
Wraps Tavily search and returns a condensed summary suitable for the agent.
"""
from __future__ import annotations

import os
from typing import Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from loopai.logger import get_logger

logger = get_logger()


class WebSearchInput(BaseModel):
    query: str = Field(..., description="Search query string")


def create_web_search_tool(tavily_api_key: Optional[str] = None):
    """Factory that returns a @tool-decorated web_search function."""

    resolved_key = tavily_api_key or os.getenv("TAVILY_API_KEY", "")

    @tool(args_schema=WebSearchInput)
    def web_search(query: str) -> str:
        """Search the web for information about a dataset using Tavily.
        Returns formatted search results including titles, URLs, and summaries.
        Use this when README or local files do not provide enough information
        about the dataset structure, fields, or intended usage."""
        import asyncio
        from loopai.agents.Obtainer.utils.web_tools import WebTools

        logger.info(f"[PostprocessAgent.web_search] query='{query}'")
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    result = pool.submit(
                        asyncio.run,
                        WebTools.search_web(query, "tavily", tavily_api_key=resolved_key),
                    ).result(timeout=60)
            else:
                result = asyncio.run(
                    WebTools.search_web(query, "tavily", tavily_api_key=resolved_key)
                )
            if not result:
                return "No search results found."
            return result[:8000]
        except Exception as e:
            logger.error(f"[PostprocessAgent.web_search] error: {e}")
            return f"Search failed: {str(e)}"

    return web_search
