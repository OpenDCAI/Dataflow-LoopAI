import os
import asyncio
from typing import Dict, Any, Optional, List
from contextlib import asynccontextmanager

from loopai.logger import get_logger

logger = get_logger()

try:
    from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Error as PlaywrightError
except ImportError:
    async_playwright = None
    Browser = None
    BrowserContext = None
    Page = None
    PlaywrightError = Exception


class PlaywrightBrowserManager:
    """Playwright browser manager for web page exploration"""
    
    def __init__(
        self,
        headless: bool = True,
        timeout: int = 30000,
        user_agent: Optional[str] = None,
        proxy: Optional[str] = None,
    ):
        """
        Initialize Playwright browser manager
        
        Args:
            headless: Whether to run browser in headless mode
            timeout: Default timeout for page operations (milliseconds)
            user_agent: Custom user agent string
            proxy: Proxy server URL (e.g., "http://127.0.0.1:7890" or "socks5://127.0.0.1:7890")
                   If None, will try to read from HTTP_PROXY, HTTPS_PROXY, or ALL_PROXY env vars
        """
        if async_playwright is None:
            raise ImportError("playwright is not installed. Please install it with: pip install playwright")
        
        self.headless = headless
        self.timeout = timeout
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/91.0.4472.124 Safari/537.36"
        )
        
        # Get proxy from parameter or environment variables
        if proxy:
            self.proxy = proxy
        else:
            # Try to get proxy from environment variables
            self.proxy = (
                os.getenv("HTTP_PROXY") or 
                os.getenv("HTTPS_PROXY") or 
                os.getenv("ALL_PROXY") or 
                os.getenv("http_proxy") or 
                os.getenv("https_proxy") or 
                os.getenv("all_proxy") or
                None
            )
        
        if self.proxy:
            logger.info(f"[PlaywrightManager] Using proxy: {self.proxy}")
        
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
    
    async def start(self):
        """Start playwright browser"""
        if self._playwright is None:
            self._playwright = await async_playwright().start()
            logger.info("[PlaywrightManager] Playwright started")
        
        if self._browser is None:
            launch_options = {"headless": self.headless}
            if self.proxy:
                # Parse proxy URL and configure proxy
                proxy_config = {"server": self.proxy}
                launch_options["proxy"] = proxy_config
                logger.info(f"[PlaywrightManager] Launching browser with proxy: {self.proxy}")
            self._browser = await self._playwright.chromium.launch(**launch_options)
            logger.info(f"[PlaywrightManager] Browser launched (headless={self.headless})")
        
        if self._context is None:
            context_options = {
                "user_agent": self.user_agent,
                "viewport": {"width": 1920, "height": 1080},
            }
            if self.proxy:
                context_options["proxy"] = {"server": self.proxy}
            self._context = await self._browser.new_context(**context_options)
            logger.info("[PlaywrightManager] Browser context created")
    
    async def get_page(self) -> Page:
        """Get or create a new page"""
        if self._page is None or self._page.is_closed():
            await self.start()
            self._page = await self._context.new_page()
            self._page.set_default_timeout(self.timeout)
            logger.info("[PlaywrightManager] New page created")
        return self._page
    
    async def close_page(self):
        """Close current page"""
        if self._page and not self._page.is_closed():
            await self._page.close()
            self._page = None
            logger.info("[PlaywrightManager] Page closed")
    
    async def close(self):
        """Close browser and cleanup"""
        try:
            if self._page and not self._page.is_closed():
                await self._page.close()
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
            logger.info("[PlaywrightManager] Browser closed and cleaned up")
        except Exception as e:
            logger.warning(f"[PlaywrightManager] Error during cleanup: {e}")
        finally:
            self._page = None
            self._context = None
            self._browser = None
            self._playwright = None
    
    @asynccontextmanager
    async def page_context(self):
        """Context manager for page operations"""
        page = await self.get_page()
        try:
            yield page
        except Exception as e:
            logger.error(f"[PlaywrightManager] Error in page context: {e}")
            raise
    
    async def get_page_info(self) -> Dict[str, Any]:
        """Get current page information"""
        try:
            page = await self.get_page()
            info = {
                "url": page.url,
                "title": await page.title(),
                "is_closed": page.is_closed(),
            }
            return info
        except Exception as e:
            logger.error(f"[PlaywrightManager] Error getting page info: {e}")
            return {
                "url": "",
                "title": "",
                "is_closed": True,
                "error": str(e),
            }
    
    def __del__(self):
        """Cleanup on deletion"""
        if self._playwright or self._browser or self._context or self._page:
            # Schedule cleanup in event loop if available
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(self.close())
                else:
                    loop.run_until_complete(self.close())
            except Exception:
                pass

