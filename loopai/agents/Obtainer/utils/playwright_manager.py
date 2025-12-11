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
    
    async def get_aria_snapshot(self, root: Optional[Any] = None) -> str:
        """
        Get accessibility tree snapshot in aria_snapshot (YAML-like) format
        
        Uses Playwright's CDP (Chrome DevTools Protocol) to get the real
        accessibility tree, which is more accurate than DOM-based parsing.
        
        Args:
            root: Optional root node selector (default: None, uses page root)
        
        Returns:
            YAML-like string representation of accessibility tree (aria_snapshot format)
        """
        try:
            page = await self.get_page()
            
            # Try to use CDP to get accessibility tree
            try:
                # Get CDP session from the page
                cdp_session = await page.context.new_cdp_session(page)
                # Get accessibility tree using CDP
                ax_tree = await cdp_session.send("Accessibility.getFullAXTree")
                await cdp_session.detach()
                
                # Convert CDP accessibility tree to aria_snapshot format
                def format_aria_snapshot_from_cdp(nodes):
                    """Convert CDP accessibility nodes to aria_snapshot YAML format"""
                    if not nodes:
                        return ""
                    
                    # Build node ID to node mapping
                    node_map = {node.get('nodeId'): node for node in nodes}
                    
                    def format_node(node, indent=0):
                        if not node:
                            return ""
                        
                        lines = []
                        # CDP format: role is an object with 'type' and sometimes 'value'
                        role_obj = node.get('role', {})
                        if isinstance(role_obj, dict):
                            role = role_obj.get('type', 'generic')
                        else:
                            role = role_obj or 'generic'
                        
                        # CDP format: name is an object with 'value'
                        name_obj = node.get('name', {})
                        if isinstance(name_obj, dict):
                            name = name_obj.get('value', '')
                        else:
                            name = name_obj or ''
                        
                        child_ids = node.get('childIds', [])
                        
                        # Format: role "name" [attributes]
                        indent_str = "  " * indent
                        if name:
                            # Escape quotes and newlines in name
                            escaped_name = name.replace('"', '\\"').replace('\n', ' ').strip()
                            if escaped_name:
                                line = f'{indent_str}- {role}: "{escaped_name}"'
                            else:
                                line = f'{indent_str}- {role}:'
                        else:
                            line = f'{indent_str}- {role}:'
                        
                        # Add level for headings
                        if role == 'heading':
                            level = 1
                            if isinstance(role_obj, dict):
                                level_val = role_obj.get('value')
                                if isinstance(level_val, dict):
                                    level = level_val.get('value', 1)
                                elif isinstance(level_val, (int, float)):
                                    level = int(level_val)
                            line += f' [level={level}]'
                        
                        lines.append(line)
                        
                        # Add children
                        for child_id in child_ids:
                            child_node = node_map.get(child_id)
                            if child_node:
                                child_lines = format_node(child_node, indent + 1)
                                if child_lines:
                                    lines.append(child_lines)
                        
                        return "\n".join(lines)
                    
                    # Find root node (usually one without parentId or the first one)
                    root_node = None
                    for node in nodes:
                        if not node.get('parentId'):
                            root_node = node
                            break
                    if not root_node and nodes:
                        root_node = nodes[0]
                    
                    if root_node:
                        return format_node(root_node)
                    return ""
                
                if ax_tree and 'nodes' in ax_tree:
                    aria_snapshot = format_aria_snapshot_from_cdp(ax_tree['nodes'])
                    if aria_snapshot:
                        logger.info(f"[PlaywrightManager] Generated aria_snapshot via CDP ({len(aria_snapshot)} chars)")
                        return aria_snapshot
                
            except Exception as cdp_error:
                logger.warning(f"[PlaywrightManager] CDP method failed, using fallback: {cdp_error}")
            
            # Fallback: Use Playwright's accessibility.snapshot() if available
            try:
                # Note: accessibility.snapshot() is deprecated but may still work
                snapshot = await page.accessibility.snapshot()
                if snapshot:
                    def format_aria_snapshot(node, indent=0):
                        """Format accessibility tree node to aria_snapshot YAML format"""
                        if not node:
                            return ""
                        
                        lines = []
                        role = node.get('role', 'generic')
                        name = node.get('name', '')
                        children = node.get('children', [])
                        
                        # Format: role "name" [attributes]
                        indent_str = "  " * indent
                        if name:
                            # Escape quotes in name
                            escaped_name = name.replace('"', '\\"').replace('\n', ' ').strip()
                            if escaped_name:
                                line = f'{indent_str}- {role}: "{escaped_name}"'
                            else:
                                line = f'{indent_str}- {role}:'
                        else:
                            line = f'{indent_str}- {role}:'
                        
                        # Add level for headings
                        if role == 'heading':
                            level = node.get('level', 1)
                            line += f' [level={level}]'
                        
                        lines.append(line)
                        
                        # Add children
                        for child in children:
                            child_lines = format_aria_snapshot(child, indent + 1)
                            if child_lines:
                                lines.append(child_lines)
                        
                        return "\n".join(lines)
                    
                    aria_snapshot = format_aria_snapshot(snapshot)
                    if aria_snapshot:
                        logger.info(f"[PlaywrightManager] Generated aria_snapshot via accessibility.snapshot() ({len(aria_snapshot)} chars)")
                        return aria_snapshot
            except Exception as acc_error:
                logger.warning(f"[PlaywrightManager] accessibility.snapshot() failed: {acc_error}")
            
            # Final fallback: Use DOM-based extraction
            logger.warning("[PlaywrightManager] Using DOM-based fallback for aria_snapshot")
            snapshot = await page.evaluate("""
                () => {
                    function getAriaRole(node) {
                        const role = node.getAttribute('role');
                        if (role) return role;
                        
                        const tagName = node.tagName.toLowerCase();
                        const roleMap = {
                            'button': 'button',
                            'a': 'link',
                            'input': node.type === 'submit' ? 'button' : (node.type === 'checkbox' ? 'checkbox' : 'textbox'),
                            'img': 'img',
                            'h1': 'heading', 'h2': 'heading', 'h3': 'heading',
                            'h4': 'heading', 'h5': 'heading', 'h6': 'heading',
                            'nav': 'navigation',
                            'main': 'main',
                            'article': 'article',
                            'section': 'region',
                            'ul': 'list', 'ol': 'list',
                            'li': 'listitem',
                            'header': 'banner',
                            'footer': 'contentinfo',
                        };
                        return roleMap[tagName] || 'generic';
                    }
                    
                    function getAriaName(node) {
                        const ariaLabel = node.getAttribute('aria-label');
                        if (ariaLabel) return ariaLabel;
                        
                        const labelledBy = node.getAttribute('aria-labelledby');
                        if (labelledBy) {
                            const labelEl = document.getElementById(labelledBy);
                            if (labelEl) return labelEl.textContent.trim();
                        }
                        
                        const text = node.textContent?.trim();
                        if (text && text.length < 200 && node.children.length === 0) return text;
                        
                        if (node.tagName === 'IMG') {
                            return node.getAttribute('alt') || '';
                        }
                        
                        return '';
                    }
                    
                    function buildTree(node, depth = 0) {
                        if (depth > 15) return null;
                        
                        const role = getAriaRole(node);
                        const name = getAriaName(node);
                        
                        if (role === 'generic' && !name && node.children.length === 0) {
                            return null;
                        }
                        
                        const result = { role: role, name: name, children: [], level: null };
                        
                        if (role === 'heading') {
                            const match = node.tagName.match(/^H([1-6])$/i);
                            result.level = match ? parseInt(match[1]) : 1;
                        }
                        
                        for (let child of node.children) {
                            const childTree = buildTree(child, depth + 1);
                            if (childTree) {
                                result.children.push(childTree);
                            }
                        }
                        
                        if (name || result.children.length > 0) {
                            return result;
                        }
                        return null;
                    }
                    
                    return buildTree(document.body);
                }
            """)
            
            def format_aria_snapshot(node, indent=0):
                """Format accessibility tree node to aria_snapshot YAML format"""
                if not node:
                    return ""
                
                lines = []
                role = node.get('role', 'generic')
                name = node.get('name', '')
                children = node.get('children', [])
                level = node.get('level')
                
                indent_str = "  " * indent
                if name:
                    escaped_name = name.replace('"', '\\"').replace('\n', ' ').strip()
                    if escaped_name:
                        line = f'{indent_str}- {role}: "{escaped_name}"'
                    else:
                        line = f'{indent_str}- {role}:'
                else:
                    line = f'{indent_str}- {role}:'
                
                if role == 'heading' and level:
                    line += f' [level={level}]'
                
                lines.append(line)
                
                for child in children:
                    child_lines = format_aria_snapshot(child, indent + 1)
                    if child_lines:
                        lines.append(child_lines)
                
                return "\n".join(lines)
            
            if snapshot:
                aria_snapshot = format_aria_snapshot(snapshot)
                logger.info(f"[PlaywrightManager] Generated aria_snapshot via DOM fallback ({len(aria_snapshot)} chars)")
                return aria_snapshot
            else:
                # Ultimate fallback
                return await page.evaluate("() => document.body.innerText")
                
        except Exception as e:
            logger.error(f"[PlaywrightManager] Error getting aria_snapshot: {e}")
            # Fallback to innerText
            try:
                page = await self.get_page()
                return await page.evaluate("() => document.body.innerText")
            except Exception:
                return ""
    
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

