"""
Kaggle dataset manager
"""
import os
import shutil
import re
import asyncio
from typing import Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page

from loopai.logger import get_logger

logger = get_logger()


class KaggleManager:
    """Kaggle dataset manager"""

    def __init__(
        self,
        disable_cache: bool = False,
        temp_base_dir: Optional[str] = None,
        kaggle_username: Optional[str] = None,
        kaggle_key: Optional[str] = None,
    ):
        """
        Initialize Kaggle Manager
        
        Args:
            disable_cache: Whether to disable cache
            temp_base_dir: Base directory for temporary files
            kaggle_username: Kaggle username (optional, can also use KAGGLE_USERNAME env var)
            kaggle_key: Kaggle API key (optional, can also use KAGGLE_KEY env var)
        """
        # Set Kaggle credentials from parameters or environment variables
        if kaggle_username:
            os.environ["KAGGLE_USERNAME"] = kaggle_username
        if kaggle_key:
            os.environ["KAGGLE_KEY"] = kaggle_key
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
            from kaggle.api.kaggle_api_extended import KaggleApi

            self.api = KaggleApi()
            self.api.authenticate()
            logger.info("[Kaggle] Authenticated with KaggleApi")
        except Exception as e:
            logger.info(f"[Kaggle] KaggleApi init/auth failed: {e}")

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

