import json
import asyncio
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field

from loopai.logger import get_logger

logger = get_logger()

try:
    from langchain_core.tools import StructuredTool
    from playwright.async_api import Page
except ImportError:
    StructuredTool = None
    Page = None


class PlaywrightActionTools:
    """Playwright action tools for agent to use"""
    
    def __init__(self, browser_manager):
        """
        Initialize Playwright action tools
        
        Args:
            browser_manager: PlaywrightBrowserManager instance
        """
        if StructuredTool is None:
            raise ImportError("langchain_core.tools is not installed")
        
        self.browser_manager = browser_manager
        self.tools = self._create_tools()
    
    def _create_tools(self) -> List:
        """Create list of langchain tools"""
        tools = [
            StructuredTool.from_function(
                func=self.search_and_submit,
                name="search_and_submit",
                description=(
                    "在页面上查找搜索框，输入搜索关键词并提交（按回车或点击搜索按钮）。"
                    "这个工具会自动查找常见的搜索框选择器（如input[type='search'], input[name='q'], input[id*='search']等），"
                    "输入文本后自动提交搜索。"
                ),
                args_schema=SearchAndSubmitInput,
            ),
            StructuredTool.from_function(
                func=self.scroll_to_bottom_multiple,
                name="scroll_to_bottom_multiple",
                description=(
                    "多次滚动页面到底部，用于加载动态内容（如无限滚动页面）。"
                    "会执行多次滚动操作，每次滚动后等待一段时间让内容加载。"
                    "适用于需要滚动才能看到更多内容的页面。"
                ),
                args_schema=ScrollToBottomInput,
            ),
            StructuredTool.from_function(
                func=self.click_element,
                name="click_element",
                description=(
                    "点击页面上的元素。可以通过CSS选择器、文本内容、或元素属性来定位元素。"
                    "支持点击链接、按钮、菜单项等。"
                ),
                args_schema=ClickElementInput,
            ),
            StructuredTool.from_function(
                func=self.navigate_to_url,
                name="navigate_to_url",
                description=(
                    "导航到指定的URL。用于跳转到新页面或刷新当前页面。"
                ),
                args_schema=NavigateToUrlInput,
            ),
            StructuredTool.from_function(
                func=self.get_page_info,
                name="get_page_info",
                description=(
                    "获取当前页面的信息，包括URL、标题、页面状态等。"
                    "用于了解当前所在页面，判断下一步操作。"
                ),
                args_schema=GetPageInfoInput,
            ),
            StructuredTool.from_function(
                func=self.wait_for_selector,
                name="wait_for_selector",
                description=(
                    "等待页面上的特定元素出现。用于等待动态加载的内容。"
                    "可以等待元素可见、可点击等状态。"
                ),
                args_schema=WaitForSelectorInput,
            ),
            StructuredTool.from_function(
                func=self.fill_input,
                name="fill_input",
                description=(
                    "在输入框中填充文本。可以通过CSS选择器定位输入框。"
                    "适用于填写表单、搜索框等场景。"
                ),
                args_schema=FillInputInput,
            ),
            StructuredTool.from_function(
                func=self.get_page_content,
                name="get_page_content",
                description=(
                    "获取当前页面的HTML内容或文本内容。用于分析页面结构，提取信息。"
                    "可以获取完整HTML或仅文本内容。"
                ),
                args_schema=GetPageContentInput,
            ),
        ]
        return tools
    
    async def search_and_submit(
        self,
        search_text: str,
        search_selector: Optional[str] = None,
    ) -> str:
        """
        Search and submit on the page
        
        Args:
            search_text: Text to search for
            search_selector: Optional CSS selector for search box (auto-detect if not provided)
        
        Returns:
            Result message
        """
        try:
            page = await self.browser_manager.get_page()
            
            # Common search box selectors
            common_selectors = [
                "input[type='search']",
                "input[name='q']",
                "input[name='search']",
                "input[id*='search']",
                "input[class*='search']",
                "input[placeholder*='搜索']",
                "input[placeholder*='search']",
                "input[placeholder*='Search']",
            ]
            
            selector = search_selector
            if not selector:
                # Try to find search box automatically
                for sel in common_selectors:
                    try:
                        element = await page.query_selector(sel)
                        if element:
                            selector = sel
                            logger.info(f"[PlaywrightTools] Found search box with selector: {selector}")
                            break
                    except Exception:
                        continue
            
            if not selector:
                # Try to find any input that might be a search box
                try:
                    inputs = await page.query_selector_all("input[type='text'], input[type='search']")
                    if inputs:
                        selector = "input[type='text'], input[type='search']"
                        logger.info(f"[PlaywrightTools] Using first available input as search box")
                except Exception:
                    pass
            
            if not selector:
                return json.dumps({
                    "success": False,
                    "error": "Could not find search box on the page",
                })
            
            # Fill search box
            await page.fill(selector, search_text)
            logger.info(f"[PlaywrightTools] Filled search box with: {search_text}")
            
            # Try to submit (press Enter or click submit button)
            try:
                await page.press(selector, "Enter")
                logger.info("[PlaywrightTools] Pressed Enter to submit search")
            except Exception:
                # Try to find and click submit button
                try:
                    submit_button = await page.query_selector(
                        "button[type='submit'], input[type='submit'], button:has-text('搜索'), "
                        "button:has-text('Search'), button:has-text('提交')"
                    )
                    if submit_button:
                        await submit_button.click()
                        logger.info("[PlaywrightTools] Clicked submit button")
                    else:
                        await page.press(selector, "Enter")
                except Exception as e:
                    logger.warning(f"[PlaywrightTools] Could not submit search: {e}")
            
            # Wait for navigation or content load
            await page.wait_for_load_state("networkidle", timeout=5000)
            
            return json.dumps({
                "success": True,
                "message": f"Successfully searched for: {search_text}",
                "current_url": page.url,
            })
            
        except Exception as e:
            logger.error(f"[PlaywrightTools] Error in search_and_submit: {e}")
            return json.dumps({
                "success": False,
                "error": str(e),
            })
    
    async def scroll_to_bottom_multiple(
        self,
        times: int = 3,
        wait_between: int = 2,
    ) -> str:
        """
        Scroll to bottom multiple times
        
        Args:
            times: Number of times to scroll
            wait_between: Seconds to wait between scrolls
        
        Returns:
            Result message
        """
        try:
            page = await self.browser_manager.get_page()
            
            for i in range(times):
                # Scroll to bottom
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                logger.info(f"[PlaywrightTools] Scrolled to bottom ({i+1}/{times})")
                
                # Wait for content to load
                if i < times - 1:  # Don't wait after last scroll
                    await asyncio.sleep(wait_between)
                    # Wait for network to be idle
                    try:
                        await page.wait_for_load_state("networkidle", timeout=3000)
                    except Exception:
                        pass
            
            return json.dumps({
                "success": True,
                "message": f"Scrolled to bottom {times} times",
                "scroll_height": await page.evaluate("document.body.scrollHeight"),
            })
            
        except Exception as e:
            logger.error(f"[PlaywrightTools] Error in scroll_to_bottom_multiple: {e}")
            return json.dumps({
                "success": False,
                "error": str(e),
            })
    
    async def click_element(
        self,
        selector: Optional[str] = None,
        text: Optional[str] = None,
        wait_after: bool = True,
    ) -> str:
        """
        Click an element on the page
        
        Args:
            selector: CSS selector for the element
            text: Text content to find and click
            wait_after: Whether to wait for navigation after click
        
        Returns:
            Result message
        """
        try:
            page = await self.browser_manager.get_page()
            
            if text and not selector:
                # Try to find element by text
                try:
                    element = page.get_by_text(text).first
                    await element.click()
                    logger.info(f"[PlaywrightTools] Clicked element with text: {text}")
                except Exception:
                    # Try using XPath
                    try:
                        xpath = f"//*[contains(text(), '{text}')]"
                        await page.click(f"xpath={xpath}")
                        logger.info(f"[PlaywrightTools] Clicked element using XPath: {xpath}")
                    except Exception as e:
                        return json.dumps({
                            "success": False,
                            "error": f"Could not find element with text '{text}': {e}",
                        })
            elif selector:
                await page.click(selector)
                logger.info(f"[PlaywrightTools] Clicked element with selector: {selector}")
            else:
                return json.dumps({
                    "success": False,
                    "error": "Either selector or text must be provided",
                })
            
            if wait_after:
                try:
                    await page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    # Fallback to load if networkidle times out
                    try:
                        await page.wait_for_load_state("load", timeout=10000)
                    except Exception:
                        # Fallback to domcontentloaded if load also times out
                        try:
                            await page.wait_for_load_state("domcontentloaded", timeout=10000)
                        except Exception:
                            pass
            
            return json.dumps({
                "success": True,
                "message": "Element clicked successfully",
                "current_url": page.url,
            })
            
        except Exception as e:
            logger.error(f"[PlaywrightTools] Error in click_element: {e}")
            return json.dumps({
                "success": False,
                "error": str(e),
            })
    
    async def navigate_to_url(
        self,
        url: str,
        wait_until: str = "networkidle",
    ) -> str:
        """
        Navigate to a URL
        
        Args:
            url: URL to navigate to
            wait_until: Wait until condition (load, domcontentloaded, networkidle)
        
        Returns:
            Result message
        """
        try:
            page = await self.browser_manager.get_page()
            # Try with specified wait_until, fallback to less strict options if timeout
            try:
                await page.goto(url, wait_until=wait_until, timeout=60000)
            except Exception as nav_error:
                # If networkidle times out, try with load
                if wait_until == "networkidle":
                    logger.warning(f"[PlaywrightTools] networkidle timeout for {url}, trying with 'load'")
                    try:
                        await page.goto(url, wait_until="load", timeout=60000)
                    except Exception as load_error:
                        # If load also times out, try domcontentloaded
                        logger.warning(f"[PlaywrightTools] load timeout for {url}, trying with 'domcontentloaded'")
                        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                elif wait_until == "load":
                    # If load times out, try domcontentloaded
                    logger.warning(f"[PlaywrightTools] load timeout for {url}, trying with 'domcontentloaded'")
                    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                else:
                    raise nav_error
            
            logger.info(f"[PlaywrightTools] Navigated to: {url}")
            
            return json.dumps({
                "success": True,
                "message": f"Navigated to {url}",
                "current_url": page.url,
                "title": await page.title(),
            })
            
        except Exception as e:
            logger.error(f"[PlaywrightTools] Error in navigate_to_url: {e}")
            return json.dumps({
                "success": False,
                "error": str(e),
            })
    
    async def get_page_info(
        self,
    ) -> str:
        """
        Get current page information
        
        Returns:
            Page information as JSON
        """
        try:
            info = await self.browser_manager.get_page_info()
            return json.dumps({
                "success": True,
                **info,
            })
            
        except Exception as e:
            logger.error(f"[PlaywrightTools] Error in get_page_info: {e}")
            return json.dumps({
                "success": False,
                "error": str(e),
            })
    
    async def wait_for_selector(
        self,
        selector: str,
        timeout: int = 10000,
        state: str = "visible",
    ) -> str:
        """
        Wait for selector to appear
        
        Args:
            selector: CSS selector to wait for
            timeout: Timeout in milliseconds
            state: Element state to wait for (visible, hidden, attached, detached)
        
        Returns:
            Result message
        """
        try:
            page = await self.browser_manager.get_page()
            await page.wait_for_selector(selector, state=state, timeout=timeout)
            logger.info(f"[PlaywrightTools] Element appeared: {selector}")
            
            return json.dumps({
                "success": True,
                "message": f"Element {selector} is now {state}",
            })
            
        except Exception as e:
            logger.error(f"[PlaywrightTools] Error in wait_for_selector: {e}")
            return json.dumps({
                "success": False,
                "error": str(e),
            })
    
    async def fill_input(
        self,
        selector: str,
        text: str,
    ) -> str:
        """
        Fill an input field
        
        Args:
            selector: CSS selector for input field
            text: Text to fill
        
        Returns:
            Result message
        """
        try:
            page = await self.browser_manager.get_page()
            await page.fill(selector, text)
            logger.info(f"[PlaywrightTools] Filled input {selector} with: {text}")
            
            return json.dumps({
                "success": True,
                "message": f"Filled input {selector}",
            })
            
        except Exception as e:
            logger.error(f"[PlaywrightTools] Error in fill_input: {e}")
            return json.dumps({
                "success": False,
                "error": str(e),
            })
    
    async def get_page_content(
        self,
        content_type: str = "text",
    ) -> str:
        """
        Get page content
        
        Args:
            content_type: Type of content to get (html, text, or both)
        
        Returns:
            Page content as JSON
        """
        try:
            page = await self.browser_manager.get_page()
            
            result = {}
            if content_type in ["html", "both"]:
                result["html"] = await page.content()
            if content_type in ["text", "both"]:
                result["text"] = await page.evaluate("() => document.body.innerText")
            
            return json.dumps({
                "success": True,
                **result,
            })
            
        except Exception as e:
            logger.error(f"[PlaywrightTools] Error in get_page_content: {e}")
            return json.dumps({
                "success": False,
                "error": str(e),
            })


# Pydantic models for tool inputs
class SearchAndSubmitInput(BaseModel):
    search_text: str = Field(description="搜索关键词")
    search_selector: Optional[str] = Field(default=None, description="搜索框的CSS选择器（可选，会自动检测）")


class ScrollToBottomInput(BaseModel):
    times: int = Field(default=3, description="滚动次数")
    wait_between: int = Field(default=2, description="每次滚动之间的等待时间（秒）")


class ClickElementInput(BaseModel):
    selector: Optional[str] = Field(default=None, description="元素的CSS选择器")
    text: Optional[str] = Field(default=None, description="要点击的元素的文本内容")
    wait_after: bool = Field(default=True, description="点击后是否等待页面加载")


class NavigateToUrlInput(BaseModel):
    url: str = Field(description="要导航到的URL")
    wait_until: str = Field(default="networkidle", description="等待条件（load, domcontentloaded, networkidle）")


class GetPageInfoInput(BaseModel):
    pass  # No input needed


class WaitForSelectorInput(BaseModel):
    selector: str = Field(description="要等待的CSS选择器")
    timeout: int = Field(default=10000, description="超时时间（毫秒）")
    state: str = Field(default="visible", description="元素状态（visible, hidden, attached, detached）")


class FillInputInput(BaseModel):
    selector: str = Field(description="输入框的CSS选择器")
    text: str = Field(description="要填充的文本")


class GetPageContentInput(BaseModel):
    content_type: str = Field(default="text", description="内容类型（html, text, both）")

