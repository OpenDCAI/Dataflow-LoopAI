import os
import sys
import shutil
import re
import json
import asyncio
from typing import Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page

from loopai.logger import get_logger

logger = get_logger()


class KaggleManager:
    """Kaggle dataset manager"""
    
    @staticmethod
    def _load_credentials_from_config() -> tuple:
        """Try to load Kaggle credentials from config file"""
        username = ""
        key = ""
        
        # Try common config file paths
        config_paths = [
            os.path.join(os.getcwd(), "examples", "config", "starter.yaml"),
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), "examples", "config", "starter.yaml"),
            os.path.expanduser("~/.config/loopai/starter.yaml"),
        ]
        
        for config_path in config_paths:
            if os.path.exists(config_path):
                try:
                    from omegaconf import OmegaConf
                    cfg = OmegaConf.load(config_path)
                    
                    # Try to get from starter.kaggle_username and starter.kaggle_key
                    if hasattr(cfg, 'starter'):
                        starter_cfg = cfg.starter
                        if hasattr(starter_cfg, 'kaggle_username'):
                            username = getattr(starter_cfg, 'kaggle_username', '') or ''
                        if hasattr(starter_cfg, 'kaggle_key'):
                            key = getattr(starter_cfg, 'kaggle_key', '') or ''
                        
                        if username and key:
                            logger.info(f"[Kaggle] Loaded credentials from config file: {config_path}")
                            break
                except Exception as e:
                    logger.debug(f"[Kaggle] Failed to load config from {config_path}: {e}")
                    continue
        
        return username, key

    def __init__(
        self,
        disable_cache: bool = False,
        temp_base_dir: Optional[str] = None,
        # 下面这两个参数虽然保留在签名里以防报错，但逻辑中不再优先使用
        kaggle_username: Optional[str] = None,
        kaggle_key: Optional[str] = None,
    ):
        """Initialize Kaggle Manager"""
        
        # --- 修改开始：只保留从配置文件读取的逻辑 ---
        final_username, final_key = self._load_credentials_from_config()
        
        # 如果配置文件里有，直接写入环境变量，供后续 KaggleApi 自动读取
        if final_username:
            os.environ["KAGGLE_USERNAME"] = final_username
        if final_key:
            os.environ["KAGGLE_KEY"] = final_key
            
        if not final_username or not final_key:
             logger.warning("[Kaggle] 未在配置文件中找到完整的 Kaggle 凭证，后续初始化可能会失败。")
        # --- 修改结束 ---

        self.disable_cache = disable_cache
        self.temp_base_dir = os.getenv("DF_TEMP_DIR") or temp_base_dir
        if self.temp_base_dir:
            os.makedirs(self.temp_base_dir, exist_ok=True)

        # Set up cache
        if disable_cache:
            import tempfile
            temp_cache = tempfile.mkdtemp(prefix="kaggle_cache_", dir=self.temp_base_dir)
            os.environ["KAGGLE_HUB_CACHE"] = temp_cache
            kaggle_config = os.path.join(temp_cache, "config")
            os.makedirs(kaggle_config, exist_ok=True)
            if "KAGGLE_CONFIG_DIR" not in os.environ:
                os.environ["KAGGLE_CONFIG_DIR"] = kaggle_config
            self._temp_cache_dir = temp_cache
            logger.info(f"[Kaggle] Cache disabled, using temp directory: {temp_cache}")
        else:
            default_cache = os.path.join(os.getcwd(), ".cache", "kaggle")
            os.makedirs(default_cache, exist_ok=True)
            os.environ["KAGGLE_HUB_CACHE"] = default_cache
            if "KAGGLE_CONFIG_DIR" not in os.environ:
                kaggle_config = os.path.join(default_cache, "config")
                os.makedirs(kaggle_config, exist_ok=True)
                os.environ["KAGGLE_CONFIG_DIR"] = kaggle_config
            self._temp_cache_dir = None
            logger.info(f"[Kaggle] Using default cache directory: {default_cache}")

        # Initialize Kaggle API
        self.api = None
        try:
            logger.info("[Kaggle] Starting Kaggle API initialization...")
            logger.info(f"[Kaggle] Python executable: {sys.executable}")
            
            # Get credentials from environment (Set by config above)
            kaggle_username_env = os.getenv("KAGGLE_USERNAME", "")
            kaggle_key_env = os.getenv("KAGGLE_KEY", "")
            
            # Ensure credentials are available
            if not kaggle_username_env or not kaggle_key_env:
                logger.warning("[Kaggle] Kaggle credentials incomplete")
                logger.warning(f"[Kaggle] Username: {'已设置' if kaggle_username_env else '未设置'}")
                logger.warning(f"[Kaggle] Key: {'已设置' if kaggle_key_env else '未设置'}")
                self.api = None
                return
            
            # Create kaggle.json config file to prevent sys.exit() in KaggleApi()
            # KaggleApi checks for kaggle.json and calls sys.exit() if not found
            kaggle_config_dir = os.getenv("KAGGLE_CONFIG_DIR", "")
            if kaggle_config_dir:
                kaggle_json_path = os.path.join(kaggle_config_dir, "kaggle.json")
            else:
                # Default location: ~/.kaggle/kaggle.json
                kaggle_home = os.path.expanduser("~/.kaggle")
                os.makedirs(kaggle_home, exist_ok=True)
                kaggle_json_path = os.path.join(kaggle_home, "kaggle.json")
            
            # Create kaggle.json if it doesn't exist or update it with provided credentials
            if not os.path.exists(kaggle_json_path) or (kaggle_username_env and kaggle_key_env):
                kaggle_config = {
                    "username": kaggle_username_env,
                    "key": kaggle_key_env
                }
                os.makedirs(os.path.dirname(kaggle_json_path), exist_ok=True)
                with open(kaggle_json_path, 'w') as f:
                    json.dump(kaggle_config, f)
                # Set restrictive permissions (Kaggle API requires this)
                os.chmod(kaggle_json_path, 0o600)
                logger.info(f"[Kaggle] Created/updated kaggle.json at: {kaggle_json_path}")
            
            logger.info("[Kaggle] Attempting to import KaggleApi...")
            from kaggle.api.kaggle_api_extended import KaggleApi
            logger.info("[Kaggle] KaggleApi imported successfully")

            logger.info("[Kaggle] Creating KaggleApi instance...")
            self.api = KaggleApi()
            logger.info("[Kaggle] KaggleApi instance created successfully")

            logger.info("[Kaggle] Calling authenticate()...")
            self.api.authenticate()
            logger.info("[Kaggle] Authenticated with KaggleApi")
        except SystemExit as e:
            logger.error(f"[Kaggle] SystemExit occurred during KaggleApi init/auth: {e}")
            logger.error("[Kaggle] This usually means KaggleApi called sys.exit() internally")
            self.api = None
        except KeyboardInterrupt as e:
            logger.error(f"[Kaggle] KeyboardInterrupt during KaggleApi init/auth: {e}")
            self.api = None
            raise  # Re-raise KeyboardInterrupt
        except BaseException as e:
            logger.error(f"[Kaggle] BaseException during KaggleApi init/auth: {type(e).__name__}: {e}", exc_info=True)
            self.api = None
        except Exception as e:
            logger.error(f"[Kaggle] Exception during KaggleApi init/auth: {type(e).__name__}: {e}", exc_info=True)
            self.api = None

    async def search_datasets(
        self, keywords: List[str], max_results: int = 5
    ) -> Dict[str, List[Dict]]:
        """Search Kaggle datasets"""
        if not self.api:
            logger.info("[Kaggle] API not initialized, skipping search")
            return {}

        results = {}
        for kw in keywords:
            try:
                items = await asyncio.wait_for(
                    asyncio.to_thread(self.api.dataset_list, search=kw), timeout=60.0
                )
                results[kw] = []
                for it in (items or [])[:max_results]:
                    ref = getattr(it, "ref", None) or f"{getattr(it, 'ownerSlug', '')}/{getattr(it, 'datasetSlug', '')}"
                    if ref and "/" in ref:
                        total_size = getattr(it, "totalBytes", 0) or getattr(it, "total_bytes", 0)
                        if not total_size and self.api:
                            try:
                                files_resp = await asyncio.wait_for(
                                    asyncio.to_thread(self.api.dataset_list_files, ref),
                                    timeout=30.0,
                                )
                                if files_resp:
                                    files = getattr(files_resp, "files", None) or []
                                    size_acc = 0
                                    for f in files:
                                        size_acc += (
                                            getattr(f, "totalBytes", 0)
                                            or getattr(f, "fileSize", 0)
                                            or getattr(f, "size", 0)
                                        )
                                    if size_acc > 0:
                                        total_size = size_acc
                            except Exception:
                                pass

                        raw_tags = getattr(it, "tags", [])
                        try:
                            tags_list = [getattr(t, "name", str(t)) for t in (raw_tags or [])]
                        except Exception:
                            tags_list = []

                        results[kw].append({
                            "id": ref,
                            "title": getattr(it, "title", ref),
                            "description": getattr(it, "description", ""),
                            "downloads": getattr(it, "usabilityRating", 0),
                            "size": total_size,
                            "tags": tags_list,
                            "owner": getattr(it, "ownerSlug", ""),
                            "url": f"https://www.kaggle.com/datasets/{ref}",
                        })
            except asyncio.TimeoutError:
                logger.info(f"[Kaggle] Search '{kw}' timed out, skipping")
                results[kw] = []
            except Exception as e:
                logger.info(f"[Kaggle] Error searching '{kw}': {e}")
                results[kw] = []

        logger.info(f"[Kaggle] Search summary: {sum(len(v) for v in results.values())} candidates")
        return results

    @staticmethod
    def _to_ref(s: str) -> Optional[str]:
        """Convert to Kaggle ref format"""
        s = (s or "").strip()
        if not s:
            return None
        if "kaggle.com/datasets/" in s:
            m = re.search(r"kaggle\.com/datasets/([^/]+)/([^/?#]+)", s)
            if not m:
                return None
            return f"{m.group(1)}/{m.group(2)}"
        if "/" in s and len(s.split("/")) == 2:
            return s
        return None

    async def try_download(
        self, page: "Page", dataset_identifier: str, save_dir: str
    ) -> Optional[str]:
        """Try downloading Kaggle dataset"""
        os.makedirs(save_dir, exist_ok=True)
        ref = self._to_ref(dataset_identifier)
        if not ref:
            logger.info(f"[Kaggle] Cannot parse dataset identifier: {dataset_identifier}")
            return None

        # Try kagglehub first
        try:
            import kagglehub
            logger.info(f"[Kaggle] Trying kagglehub download: {ref}")
            path = await asyncio.to_thread(kagglehub.dataset_download, ref)
            if path and os.path.exists(path):
                logger.info(f"[Kaggle] kagglehub download completed: {path}")
                if os.path.abspath(path) != os.path.abspath(save_dir):
                    try:
                        if os.path.isfile(path):
                            dest_path = os.path.join(save_dir, os.path.basename(path))
                            shutil.move(path, dest_path)
                            logger.info(f"[Kaggle] Moved file to: {dest_path}")
                            return dest_path
                        elif os.path.isdir(path):
                            for item in os.listdir(path):
                                src_item = os.path.join(path, item)
                                dst_item = os.path.join(save_dir, item)
                                if os.path.isdir(src_item):
                                    shutil.copytree(src_item, dst_item, dirs_exist_ok=True)
                                else:
                                    shutil.copy2(src_item, dst_item)
                            logger.info(f"[Kaggle] Copied content to: {save_dir}")
                            if self.disable_cache:
                                try:
                                    shutil.rmtree(path, ignore_errors=True)
                                except Exception:
                                    pass
                            return save_dir
                    except Exception as move_e:
                        logger.info(f"[Kaggle] Error moving/copying: {move_e}")
                return path
        except Exception as e:
            logger.info(f"[Kaggle] kagglehub failed: {e}, trying KaggleApi")

        # Fallback to KaggleApi
        if self.api:
            try:
                logger.info(f"[Kaggle] Using KaggleApi download: {ref}")
                await asyncio.wait_for(
                    asyncio.to_thread(
                        self.api.dataset_download_files, ref, path=save_dir, unzip=True, quiet=False
                    ),
                    timeout=60.0,
                )
                logger.info(f"[Kaggle] Download completed to: {save_dir}")
                return save_dir
            except asyncio.TimeoutError:
                logger.info(f"[Kaggle] API download timed out")
            except Exception as e:
                logger.info(f"[Kaggle] API download failed: {e}")

        # Clean up temp cache if disabled
        if self.disable_cache and hasattr(self, "_temp_cache_dir") and self._temp_cache_dir:
            try:
                if os.path.exists(self._temp_cache_dir):
                    shutil.rmtree(self._temp_cache_dir, ignore_errors=True)
            except Exception:
                pass

        return None