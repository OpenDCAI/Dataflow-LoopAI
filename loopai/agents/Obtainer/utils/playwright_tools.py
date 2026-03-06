import json
import asyncio
import os
import sys
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field

from loopai.logger import get_logger

logger = get_logger()

try:
    from langchain_core.tools import StructuredTool
except ImportError:
    StructuredTool = None

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False
    logger.warning("mcp library not available. Install with: pip install mcp")


class MCPConnectionManager:
    """Manages persistent MCP connection"""
    
    def __init__(self, server_path: str = "npx", server_args: Optional[List[str]] = None):
        self.server_path = server_path
        self.server_args = server_args or ["-y", "@playwright/mcp@latest"]
        self._read = None
        self._write = None
        self._session: Optional[Any] = None # Changed type hint to Any to avoid NameError
        self._stdio_context_manager = None
        self._session_context_manager = None
        self._connected = False
        self._lock = asyncio.Lock()
        self._shutdown = False
    
    async def connect(self):
        """Establish MCP connection"""
        async with self._lock:
            if self._connected:
                return
            
            if self._shutdown:
                raise RuntimeError("Connection manager has been shut down")
            
            try:
                # Add headless flag and other options for playwright-mcp
                server_args_with_options = self.server_args.copy() if self.server_args else []
                
                # --- 修正：只添加官方支持的参数 ---
                # 1. 找到包名的位置
                pkg_index = -1
                for i, arg in enumerate(server_args_with_options):
                    if "@playwright/mcp" in arg:
                        pkg_index = i
                        break
                
                insert_pos = pkg_index + 1 if pkg_index >= 0 else len(server_args_with_options)
                
                # 2. 仅添加 --isolated (这是官方明确支持的)
                if "--isolated" not in server_args_with_options:
                    server_args_with_options.insert(insert_pos, "--isolated")

                # 注意：移除了 --no-sandbox 和 --disable-gpu，因为官方包不支持通过 CLI 传递这些
                
                # --- 3. 关键修复：清洗环境变量 (解决 Invalid URL 问题) ---
                env = os.environ.copy()
                
                # 强制 Headless
                if "HEADLESS" not in env:
                    env["HEADLESS"] = "true"

                # 移除所有可能导致解析错误的代理变量
                # 这一步是解决 "SyntaxError: browserType.launch: Invalid URL" 的关键
                proxy_keys = ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]
                for key in proxy_keys:
                    if key in env:
                        # 仅记录调试信息，实际运行中删除
                        logger.warning(f"[MCPConnectionManager] Removing env var {key} to prevent Playwright URL parsing errors")
                        del env[key]
                
                logger.info(f"[MCPConnectionManager] Launching server: {self.server_path} {' '.join(server_args_with_options)}")
                
                server_params = StdioServerParameters(
                    command=self.server_path,
                    args=server_args_with_options,
                    env=env
                )
                
                self._stdio_context_manager = stdio_client(server_params)
                self._read, self._write = await self._stdio_context_manager.__aenter__()
                
                self._session_context_manager = ClientSession(self._read, self._write)
                self._session = await self._session_context_manager.__aenter__()
                await self._session.initialize()
                
                self._connected = True
                logger.info("[MCPConnectionManager] Connected to playwright-mcp server")
                
            except Exception as e:
                logger.error(f"[MCPConnectionManager] Error connecting: {e}")
                await self._cleanup_resources()
                raise
    
    async def _cleanup_resources(self):
        """Internal cleanup of resources"""
        # Reset state first to prevent further operations
        self._connected = False
        self._session = None
        
        # Exit session context
        if self._session_context_manager:
            try:
                await self._session_context_manager.__aexit__(None, None, None)
            except RuntimeError as e:
                if "different task" in str(e) or "cancel scope" in str(e).lower():
                    logger.debug("[MCPConnectionManager] Session context already closed in different task")
                else:
                    logger.debug(f"[MCPConnectionManager] Error exiting session context: {e}")
            except Exception as e:
                logger.debug(f"[MCPConnectionManager] Error exiting session context: {e}")
            finally:
                self._session_context_manager = None
        
        # Exit stdio context
        if self._stdio_context_manager:
            try:
                await self._stdio_context_manager.__aexit__(None, None, None)
            except RuntimeError as e:
                if "different task" in str(e) or "cancel scope" in str(e).lower():
                    logger.debug("[MCPConnectionManager] Stdio context already closed in different task")
                else:
                    logger.debug(f"[MCPConnectionManager] Error exiting stdio context: {e}")
            except Exception as e:
                logger.debug(f"[MCPConnectionManager] Error exiting stdio context: {e}")
            finally:
                self._stdio_context_manager = None
        
        self._read = None
        self._write = None
    
    async def disconnect(self):
        """Close MCP connection"""
        async with self._lock:
            if not self._connected:
                return
            
            try:
                await self._cleanup_resources()
                logger.info("[MCPConnectionManager] Disconnected from playwright-mcp server")
            except Exception as e:
                logger.warning(f"[MCPConnectionManager] Error disconnecting: {e}")
    
    async def shutdown(self):
        """Shutdown connection manager (prevents reconnection)"""
        self._shutdown = True
        await self.disconnect()
    
    @property
    def session(self) -> Optional[Any]: # Changed to Any
        """Get current session"""
        return self._session if self._connected else None
    
    @property
    def is_connected(self) -> bool:
        """Check if connected"""
        return self._connected


class PlaywrightActionTools:
    """Playwright action tools using playwright-mcp via MCP protocol"""
    
    def __init__(self, browser_manager=None, mcp_server_path: Optional[str] = None):
        """
        Initialize Playwright action tools with MCP
        
        Args:
            browser_manager: PlaywrightBrowserManager instance (kept for compatibility)
            mcp_server_path: Path to playwright-mcp server (default: uses npx @playwright/mcp)
        """
        if StructuredTool is None:
            raise ImportError("langchain_core.tools is not installed")
        
        if not MCP_AVAILABLE:
            raise ImportError("mcp library is not installed. Install with: pip install mcp")
        
        self.browser_manager = browser_manager  # Kept for compatibility
        # Add --isolated flag to allow multiple browser instances
        server_args = ["-y", "@playwright/mcp@latest", "--isolated"] if (mcp_server_path == "npx" or mcp_server_path is None) else []
        
        # Determine strict server path
        actual_server_path = mcp_server_path or "npx"
        
        self.connection_manager = MCPConnectionManager(
            server_path=actual_server_path,
            server_args=server_args
        )
        self.tools: List = []
        self._tools_initialized = False
        self._init_lock = asyncio.Lock()
    
    async def _initialize_tools(self):
        """Initialize MCP connection and create tools"""
        async with self._init_lock:
            if self._tools_initialized:
                return
            
            try:
                # Connect to MCP server
                await self.connection_manager.connect()
                session = self.connection_manager.session
                
                if not session:
                    raise RuntimeError("Failed to establish MCP connection")
                
                # List available tools
                tools_result = await session.list_tools()
                logger.info(f"[PlaywrightTools] MCP tools available: {[tool.name for tool in tools_result.tools]}")
                
                # Create langchain tools from MCP tools
                self.tools = await self._create_tools_from_mcp(tools_result.tools, session)
                self._tools_initialized = True
                
            except Exception as e:
                logger.error(f"[PlaywrightTools] Error initializing tools: {e}")
                raise
    
    async def _create_tools_from_mcp(self, mcp_tools: List, session: Any) -> List: # Changed ClientSession to Any
        """Create langchain tools from MCP tools, filtering to only essential tools"""
        
        # Define allowed tool names (case-insensitive matching)
        allowed_tools = {
            'navigate', 'goto', 'navigate_to', 'go_to', 'browser_navigate',
            'browser_click', 'click', 'click_element',
            'browser_fill_form', 'fill_form',
            'browser_press_key', 'press_key', 'press',
            'browser_snapshot', 'snapshot',
            'browser_tabs' # Added checking for tabs as well
        }
        
        filtered_tools = []
        for mcp_tool in mcp_tools:
            tool_name_lower = mcp_tool.name.lower()
            if any(allowed in tool_name_lower for allowed in allowed_tools):
                filtered_tools.append(mcp_tool)
                logger.info(f"[PlaywrightTools] Including tool: {mcp_tool.name}")
            else:
                logger.debug(f"[PlaywrightTools] Filtering out tool: {mcp_tool.name}")
        
        logger.info(f"[PlaywrightTools] Filtered {len(mcp_tools)} tools down to {len(filtered_tools)} essential tools")
        
        tools = []
        
        def create_tool_func(tool_name: str, tool_description: str, mcp_session: Any): # Changed ClientSession to Any
            """Create a tool function with proper closure"""
            async def async_tool_func(**kwargs):
                """Execute MCP tool (async)"""
                import time
                start_time = time.time()
                try:
                    logger.debug(f"[PlaywrightTools] Calling MCP tool {tool_name} with args: {kwargs}")
                    
                    # Add timeout (60 seconds per tool call)
                    try:
                        result = await asyncio.wait_for(
                            mcp_session.call_tool(
                                tool_name,
                                arguments=kwargs or {}
                            ),
                            timeout=60.0
                        )
                    except asyncio.TimeoutError:
                        elapsed = time.time() - start_time
                        logger.error(f"[PlaywrightTools] Tool call {tool_name} timed out after 60s (actual: {elapsed:.2f}s)")
                        return json.dumps({"success": False, "error": "Tool call timed out"})
                    except Exception as e:
                        logger.error(f"[PlaywrightTools] Tool call {tool_name} error: {e}")
                        raise
                    
                    elapsed = time.time() - start_time
                    logger.debug(f"[PlaywrightTools] MCP tool {tool_name} completed in {elapsed:.2f}s")
                    
                    # Format result
                    if hasattr(result, 'content'):
                        content = result.content
                        if isinstance(content, list) and len(content) > 0:
                            item = content[0]
                            if hasattr(item, 'text'):
                                result_text = item.text
                            elif hasattr(item, 'data'):
                                result_text = json.dumps(item.data) if isinstance(item.data, dict) else str(item.data)
                            else:
                                result_text = str(item)
                        else:
                            result_text = str(content)
                    else:
                        result_text = str(result)
                    
                    return json.dumps({
                        "success": True,
                        "result": result_text
                    })
                    
                except Exception as e:
                    logger.error(f"[PlaywrightTools] Error calling MCP tool {tool_name}: {e}")
                    return json.dumps({
                        "success": False,
                        "error": str(e)
                    })
            
            # Create synchronous wrapper
            def sync_tool_func(**kwargs):
                try:
                    try:
                        loop = asyncio.get_running_loop()
                        # We are in an async context, run in thread
                        import threading
                        result_container = {}
                        exception_container = {}
                        
                        def run_in_thread():
                            try:
                                new_loop = asyncio.new_event_loop()
                                asyncio.set_event_loop(new_loop)
                                result_container['result'] = new_loop.run_until_complete(async_tool_func(**kwargs))
                                new_loop.close()
                            except Exception as e:
                                exception_container['exception'] = e
                        
                        thread = threading.Thread(target=run_in_thread)
                        thread.start()
                        thread.join(timeout=300)
                        
                        if thread.is_alive():
                            raise TimeoutError(f"Tool {tool_name} execution timed out")
                        
                        if 'exception' in exception_container:
                            raise exception_container['exception']
                        
                        return result_container.get('result')
                    except RuntimeError:
                        # No running loop, use asyncio.run
                        return asyncio.run(async_tool_func(**kwargs))
                except Exception as e:
                    logger.error(f"[PlaywrightTools] Error in sync wrapper for {tool_name}: {e}")
                    return json.dumps({"success": False, "error": str(e)})
            
            import functools
            sync_tool_func = functools.wraps(async_tool_func)(sync_tool_func)
            sync_tool_func.ainvoke = async_tool_func
            
            # Double safety check to ensure it's not a coroutine
            if asyncio.iscoroutinefunction(sync_tool_func):
                original = sync_tool_func
                def force_sync(**kwargs):
                    res = original(**kwargs)
                    if asyncio.iscoroutine(res):
                        return asyncio.run(res)
                    return res
                force_sync.ainvoke = async_tool_func
                return force_sync

            return sync_tool_func
        
        for mcp_tool in filtered_tools:
            try:
                tool_name = mcp_tool.name
                tool_description = mcp_tool.description or f"MCP tool: {mcp_tool.name}"
                
                tool_func = create_tool_func(tool_name, tool_description, session)
                tool_schema = self._create_schema_from_mcp_tool(mcp_tool)
                
                tool = StructuredTool.from_function(
                    func=tool_func,
                    name=tool_name,
                    description=tool_description,
                    args_schema=tool_schema if tool_schema else None,
                )
                tools.append(tool)
            except Exception as e:
                logger.warning(f"[PlaywrightTools] Failed to create tool {mcp_tool.name}: {e}")
                continue
        
        return tools
    
    def _create_schema_from_mcp_tool(self, mcp_tool):
        """Create Pydantic schema from MCP tool input schema"""
        if not hasattr(mcp_tool, 'inputSchema') or not mcp_tool.inputSchema:
            return None
        
        try:
            input_schema = mcp_tool.inputSchema
            properties = input_schema.get('properties', {})
            required = input_schema.get('required', [])
            
            field_definitions = {}
            for prop_name, prop_info in properties.items():
                prop_type = prop_info.get('type', 'string')
                description = prop_info.get('description', '')
                default = prop_info.get('default', ...)
                
                python_type = str
                if prop_type == 'integer':
                    python_type = int
                elif prop_type == 'number':
                    python_type = float
                elif prop_type == 'boolean':
                    python_type = bool
                elif prop_type == 'array':
                    python_type = list
                elif prop_type == 'object':
                    python_type = dict
                
                if prop_name not in required and default == ...:
                    default = None
                
                field_definitions[prop_name] = (
                    Optional[python_type] if prop_name not in required else python_type,
                    Field(
                        default=default if default != ... else ...,
                        description=description
                    )
                )
            
            if field_definitions:
                schema_class = type(
                    f"{mcp_tool.name}Input",
                    (BaseModel,),
                    {
                        "__annotations__": {k: v[0] for k, v in field_definitions.items()},
                        **{k: v[1] for k, v in field_definitions.items()}
                    }
                )
                return schema_class
            return None
            
        except Exception as e:
            logger.warning(f"[PlaywrightTools] Failed to create schema for {mcp_tool.name}: {e}")
            return None
    
    async def get_tools(self) -> List:
        """Get list of langchain tools (async initialization)"""
        if not self._tools_initialized:
            await self._initialize_tools()
        return self.tools
    
    def _create_tools(self) -> List:
        """Synchronous placeholder (tools populated via async init)"""
        return []
    
    async def close(self):
        """Close MCP connection"""
        try:
            await self.connection_manager.shutdown()
        except Exception as e:
            logger.warning(f"[PlaywrightTools] Error closing MCP connection: {e}")
        finally:
            self._tools_initialized = False


# Pydantic models for compatibility (unchanged)
class SearchAndSubmitInput(BaseModel):
    search_text: str = Field(description="搜索关键词")
    search_selector: Optional[str] = Field(default=None, description="搜索框的CSS选择器")

class ScrollToBottomInput(BaseModel):
    times: int = Field(default=3, description="滚动次数")
    wait_between: int = Field(default=2, description="每次滚动之间的等待时间（秒）")

class ClickElementInput(BaseModel):
    selector: Optional[str] = Field(default=None, description="元素的CSS选择器")
    text: Optional[str] = Field(default=None, description="要点击的元素的文本内容")
    wait_after: bool = Field(default=True, description="点击后是否等待页面加载")

class NavigateToUrlInput(BaseModel):
    url: str = Field(description="要导航到的URL")
    wait_until: str = Field(default="networkidle", description="等待条件")

class GetPageInfoInput(BaseModel):
    pass

class WaitForSelectorInput(BaseModel):
    selector: str = Field(description="要等待的CSS选择器")
    timeout: int = Field(default=10000, description="超时时间")
    state: str = Field(default="visible", description="元素状态")

class FillInputInput(BaseModel):
    selector: str = Field(description="输入框的CSS选择器")
    text: str = Field(description="要填充的文本")

class GetPageContentInput(BaseModel):
    content_type: str = Field(default="text", description="内容类型")