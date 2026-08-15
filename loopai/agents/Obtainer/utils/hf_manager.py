import os
import shutil
import asyncio
from typing import Dict, List, Optional

try:
    import tenacity
except ImportError:
    tenacity = None

try:
    import requests
except ImportError:
    requests = None

from loopai.logger import get_logger

logger = get_logger()


class HuggingFaceManager:
    """HuggingFace dataset manager"""

    def __init__(
        self,
        max_retries: int = 2,
        retry_delay: int = 5,
        disable_cache: bool = False,
        temp_base_dir: Optional[str] = None,
    ):
        """Initialize HuggingFace Manager"""
        from loopai.utils.hf_endpoints import (
            apply_hf_endpoint,
            endpoint_reachable,
            hf_endpoint_chain,
        )

        self.endpoint_chain = hf_endpoint_chain()
        self._endpoint_index = 0
        self.hf_endpoint = ""
        for candidate in self.endpoint_chain:
            if endpoint_reachable(candidate):
                self.hf_endpoint = candidate
                break
        if not self.hf_endpoint:
            self.hf_endpoint = self.endpoint_chain[0]
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.disable_cache = disable_cache
        apply_hf_endpoint(self.hf_endpoint)
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

        # Set up temp directory
        self.temp_base_dir = os.getenv("DF_TEMP_DIR") or temp_base_dir
        if self.temp_base_dir:
            os.makedirs(self.temp_base_dir, exist_ok=True)

        # Set up cache
        if disable_cache:
            import tempfile
            temp_cache = tempfile.mkdtemp(prefix="hf_cache_", dir=self.temp_base_dir)
            os.environ["HF_HOME"] = temp_cache
            os.environ["HUGGINGFACE_HUB_CACHE"] = os.path.join(temp_cache, "hub")
            os.environ["HF_DATASETS_CACHE"] = os.path.join(temp_cache, "datasets")
            os.environ["TRANSFORMERS_CACHE"] = os.path.join(temp_cache, "transformers")
            self._temp_cache_dir = temp_cache
            logger.info(f"[HuggingFace] Cache disabled, using temp directory: {temp_cache}")
        else:
            default_cache = os.path.join(os.getcwd(), ".cache", "hf")
            os.makedirs(default_cache, exist_ok=True)
            os.environ["HF_HOME"] = default_cache
            os.environ["HUGGINGFACE_HUB_CACHE"] = os.path.join(default_cache, "hub")
            os.environ["HF_DATASETS_CACHE"] = os.path.join(default_cache, "datasets")
            os.environ["TRANSFORMERS_CACHE"] = os.path.join(default_cache, "transformers")
            self._temp_cache_dir = None
            logger.info(f"[HuggingFace] Using default cache directory: {default_cache}")

        # Lazy import HuggingFace dependencies
        try:
            from huggingface_hub import HfApi, snapshot_download
            from datasets import get_dataset_config_names

            self.hf_api = HfApi(endpoint=self.hf_endpoint)
            self._snapshot_download = snapshot_download
            self._get_dataset_config_names = get_dataset_config_names
        except ImportError as e:
            logger.error(f"[HuggingFace] Failed to import dependencies: {e}")
            self.hf_api = None
            self._snapshot_download = None
            self._get_dataset_config_names = None

    @staticmethod
    def _is_retryable_error(e: Exception) -> bool:
        """Check if error is retryable"""
        if requests:
            if isinstance(
                e,
                (
                    ConnectionResetError,
                    ConnectionRefusedError,
                    requests.exceptions.Timeout,
                    requests.exceptions.ConnectionError,
                ),
            ):
                return True

            if isinstance(e, requests.exceptions.HTTPError) and e.response.status_code in [
                502,
                503,
                504,
            ]:
                return True

        error_str = str(e).lower()
        if any(
            err_msg in error_str
            for err_msg in ["10054", "connection reset by peer", "timeout", "serviceunavailable"]
        ):
            return True

        return False

    async def _retry_async_thread(self, func, *args, **kwargs):
        """Retry async thread execution"""
        if tenacity is None:
            # If tenacity not available, just execute once
            return await asyncio.to_thread(func, *args, **kwargs)
        
        def log_retry_attempt(retry_state):
            attempt = retry_state.attempt_number
            exception = retry_state.outcome.exception()
            logger.info(
                f"[HuggingFace] Retryable network error (Attempt {attempt}/{self.max_retries}): {exception}"
            )

        retryer = tenacity.AsyncRetrying(
            stop=tenacity.stop_after_attempt(self.max_retries),
            wait=tenacity.wait_incrementing(
                start=self.retry_delay, increment=self.retry_delay
            ),
            retry=tenacity.retry_if_exception(self._is_retryable_error),
            before_sleep=log_retry_attempt,
            reraise=True,
        )

        async def func_to_retry():
            return await asyncio.to_thread(func, *args, **kwargs)

        try:
            return await retryer(func_to_retry)
        except tenacity.RetryError as e:
            logger.info(f"[HuggingFace] All {self.max_retries} retries failed")
            if e.last_attempt and e.last_attempt.failed:
                raise e.last_attempt.exception
            else:
                raise Exception(f"HuggingFace operation failed ({func.__name__})")
        except Exception as e:
            logger.info(f"[HuggingFace] Non-retryable error: {e}")
            raise e

    def _next_endpoint(self) -> Optional[str]:
        """Advance to the next mirror in the fallback chain and rebuild the API.

        Returns ``None`` once every endpoint has been tried, so callers can
        stop retrying instead of looping forever.
        """
        if self._endpoint_index + 1 >= len(self.endpoint_chain):
            return None
        self._endpoint_index += 1
        endpoint = self.endpoint_chain[self._endpoint_index]
        self.hf_endpoint = endpoint
        from loopai.utils.hf_endpoints import apply_hf_endpoint

        apply_hf_endpoint(endpoint)
        try:
            from huggingface_hub import HfApi

            self.hf_api = HfApi(endpoint=endpoint)
        except Exception as exc:  # pragma: no cover - defensive
            logger.error(f"[HuggingFace] Failed to rebuild API for {endpoint}: {exc}")
            self.hf_api = None
        return endpoint

    async def search_datasets(
        self, keywords: List[str], max_results: int = 5
    ) -> Dict[str, List[Dict]]:
        """Search HuggingFace datasets"""
        if not self.hf_api:
            logger.error("[HuggingFace] API not initialized")
            return {}

        results = {}
        for keyword in keywords:
            found = False
            for _ in range(len(self.endpoint_chain)):
                try:
                    logger.info(f"[HuggingFace] Searching keyword: '{keyword}'")
                    datasets = await self._retry_async_thread(
                        self.hf_api.list_datasets, search=keyword, limit=max_results
                    )

                    results[keyword] = []
                except Exception as exc:
                    logger.info(
                        f"[HuggingFace] Error searching '{keyword}' via "
                        f"{self.hf_endpoint}: {exc}"
                    )
                    if self._next_endpoint() is None:
                        break
                    continue
                for dataset in datasets:
                    dataset_size = None
                    try:
                        if hasattr(dataset, "siblings"):
                            total_size = 0
                            for sibling in getattr(dataset, "siblings", []):
                                if hasattr(sibling, "size") and sibling.size:
                                    total_size += sibling.size
                            if total_size > 0:
                                dataset_size = total_size
                        if not dataset_size and hasattr(dataset, "size"):
                            dataset_size = getattr(dataset, "size", None)
                    except Exception:
                        pass

                    results[keyword].append({
                        "id": dataset.id,
                        "title": getattr(dataset, "title", dataset.id),
                        "description": getattr(dataset, "description", ""),
                        "downloads": getattr(dataset, "downloads", 0),
                        "tags": getattr(dataset, "tags", []),
                        "size": dataset_size,
                    })

                logger.info(f"[HuggingFace] Found {len(results[keyword])} datasets")
                found = True
                break
            if not found:
                results[keyword] = []

        return results

    async def download_dataset(self, dataset_id: str, save_dir: str) -> Optional[str]:
        """Download HuggingFace dataset with multi-level endpoint fallback."""
        if not self._snapshot_download:
            logger.error("[HuggingFace] Snapshot download not available")
            return None

        for _ in range(len(self.endpoint_chain)):
            try:
                logger.info(f"[HuggingFace] Starting download: {dataset_id}")
                dataset_dir = os.path.join(save_dir, dataset_id.replace("/", "_"))
                os.makedirs(dataset_dir, exist_ok=True)

                config_to_load = None
                try:
                    logger.info(f"[HuggingFace] Checking configs for {dataset_id}...")
                    if self._get_dataset_config_names:
                        configs = await self._retry_async_thread(
                            self._get_dataset_config_names, path=dataset_id
                        )
                        if configs:
                            config_to_load = configs[0]
                            logger.info(
                                f"[HuggingFace] Dataset {dataset_id} has {len(configs)} configs. "
                                f"Auto-selecting first: {config_to_load}"
                            )
                except Exception as e:
                    logger.info(f"[HuggingFace] Error checking configs: {e}")

                logger.info(f"[HuggingFace] Starting download of all files for {dataset_id}...")
                returned_path = await self._retry_async_thread(
                    self._snapshot_download,
                    repo_id=dataset_id,
                    local_dir=dataset_dir,
                    repo_type="dataset",
                    force_download=True,
                    endpoint=self.hf_endpoint,
                )

                # Clean up temp cache if disabled
                if self.disable_cache and hasattr(self, "_temp_cache_dir") and self._temp_cache_dir:
                    try:
                        if os.path.exists(self._temp_cache_dir):
                            shutil.rmtree(self._temp_cache_dir, ignore_errors=True)
                            logger.info(f"[HuggingFace] Cleaned temp cache: {self._temp_cache_dir}")
                    except Exception as e:
                        logger.info(f"[HuggingFace] Error cleaning cache: {e}")

                config_str = f"(config: {config_to_load})" if config_to_load else "(default config)"
                logger.info(
                    f"[HuggingFace] Dataset {dataset_id} {config_str} downloaded successfully to {returned_path}"
                )
                return returned_path
            except Exception as e:
                logger.info(
                    f"[HuggingFace] Download failed for {dataset_id} via "
                    f"{self.hf_endpoint}: {e}"
                )
                if self._next_endpoint() is None:
                    break
        return None

