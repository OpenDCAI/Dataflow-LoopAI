import os
import re
import shutil
import hashlib
import asyncio
import time
from typing import Dict, List, Any, Optional
from datetime import datetime

from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import Chroma

# Try to import RecursiveCharacterTextSplitter from new location first
try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError:
    # Fallback to old location for backward compatibility
    try:
        from langchain.text_splitter import RecursiveCharacterTextSplitter
    except ImportError:
        raise ImportError(
            "Failed to import RecursiveCharacterTextSplitter. "
            "Please install langchain-text-splitters: pip install langchain-text-splitters"
        )

# Try to import Document from new location first
try:
    from langchain_core.documents import Document
except ImportError:
    # Fallback to old location for backward compatibility
    try:
        from langchain.schema import Document
    except ImportError:
        raise ImportError(
            "Failed to import Document. "
            "Please install langchain-core: pip install langchain-core"
        )

from loopai.logger import get_logger

logger = get_logger()


class RAGManager:
    """RAG Manager for storing and retrieving web content"""

    def __init__(
        self,
        api_base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        *,
        embed_model: Optional[str] = None,
        persist_directory: str = "./rag_db",
        reset: bool = False,
        collection_name: str = "rag_collection",
    ):
        """Initialize RAG Manager"""
        resolved_api_base = api_base_url or os.getenv("RAG_API_URL")
        resolved_api_key = api_key or os.getenv("RAG_API_KEY")
        resolved_embed_model = embed_model or os.getenv("RAG_EMB_MODEL") or "text-embedding-3-large"

        if not resolved_api_base or not resolved_api_key:
            raise ValueError(
                "RAG initialization failed: Missing API base URL or API Key. "
                "Please provide them during initialization or set environment variables."
            )

        logger.info(
            f"[RAG] Initializing RAG manager, storage directory: {persist_directory}, "
            f"model: {resolved_embed_model}"
        )
        self.embeddings = OpenAIEmbeddings(
            openai_api_base=resolved_api_base,
            openai_api_key=resolved_api_key,
            model=resolved_embed_model
        )
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=600,
            chunk_overlap=120,
            length_function=len,
            separators=["\n\n", "\n", "。", "！", "？", ". ", "! ", "? ", " ", ""]
        )
        self.persist_directory = persist_directory
        self.vectorstore = None
        self.collection_name = collection_name
        self.document_count = 0
        
        # Reset if requested
        if reset and os.path.exists(persist_directory):
            shutil.rmtree(persist_directory)
        os.makedirs(persist_directory, exist_ok=True)
        
        # Initialize vector store
        try:
            self.vectorstore = Chroma(
                collection_name=self.collection_name,
                embedding_function=self.embeddings,
                persist_directory=self.persist_directory
            )
        except Exception as e:
            logger.error(f"[RAG] Failed to initialize vector store: {e}")
            self.vectorstore = None
        
        # Deduplication set
        self._seen_hashes = set()
        
        # Lock for thread-safe ChromaDB operations
        self._write_lock = asyncio.Lock()
        
        # Batch persist configuration
        self._pending_persist = False
        self._last_persist_time = 0
        self._persist_interval = 5.0  # Persist every 5 seconds or after 10 documents
        self._documents_since_persist = 0
        self._persist_batch_size = 10

    async def add_webpage_content(
        self, url: str, text_content: str, metadata: Optional[Dict[str, Any]] = None
    ):
        """Add webpage content to RAG with thread-safe operations and retry mechanism"""
        if not text_content or len(text_content.strip()) < 50:
            logger.info(f"[RAG] Skipping webpage with too short content: {url}")
            return
        
        # Use lock to serialize all ChromaDB operations
        async with self._write_lock:
            try:
                logger.info(f"[RAG] Adding webpage content: {url} (length: {len(text_content)} chars)")
                # Basic cleaning
                cleaned = re.sub(r"\s+", " ", text_content).strip()
                chunks = self.text_splitter.split_text(cleaned)
                logger.info(f"[RAG] Text split into {len(chunks)} chunks")
                
                documents = []
                for i, chunk in enumerate(chunks):
                    if not chunk or len(chunk.strip()) < 80:
                        continue
                    # Content deduplication
                    digest = hashlib.sha1(chunk.strip().encode("utf-8")).hexdigest()
                    if digest in self._seen_hashes:
                        continue
                    self._seen_hashes.add(digest)
                    
                    doc_metadata = {
                        "source_url": url,
                        "chunk_id": i,
                        "total_chunks": len(chunks),
                        "timestamp": datetime.now().isoformat()
                    }
                    if metadata:
                        doc_metadata.update(metadata)
                    documents.append(Document(page_content=chunk, metadata=doc_metadata))
                
                if not documents:
                    logger.warning(f"[RAG] No valid document chunks after cleaning/deduplication: {url}")
                    return
                
                # Retry mechanism for ChromaDB operations
                max_retries = 3
                retry_delay = 0.5
                documents_added = False
                last_error = None
                
                for attempt in range(max_retries):
                    try:
                        if self.vectorstore is None:
                            # Fallback: create if initialization failed
                            self.vectorstore = await asyncio.to_thread(
                                Chroma.from_documents,
                                documents=documents,
                                embedding=self.embeddings,
                                persist_directory=self.persist_directory,
                            )
                            documents_added = True
                        else:
                            await asyncio.to_thread(self.vectorstore.add_documents, documents)
                            documents_added = True
                        
                        # Success, break out of retry loop
                        last_error = None
                        break
                        
                    except Exception as e:
                        last_error = e
                        error_str = str(e).lower()
                        
                        # Check if it's a compaction error (retryable)
                        if "compaction" in error_str or "metadata segment" in error_str:
                            if attempt < max_retries - 1:
                                wait_time = retry_delay * (2 ** attempt)  # Exponential backoff
                                logger.warning(
                                    f"[RAG] ChromaDB compaction error (attempt {attempt + 1}/{max_retries}), "
                                    f"retrying in {wait_time}s: {e}"
                                )
                                await asyncio.sleep(wait_time)
                                continue
                            else:
                                logger.error(f"[RAG] ChromaDB compaction error after {max_retries} attempts: {e}")
                        else:
                            # Non-retryable error, raise immediately
                            raise
                
                if not documents_added:
                    # All retries failed, cannot add documents
                    logger.error(f"[RAG] Failed to add documents after {max_retries} attempts: {last_error}")
                    # Return early, don't update counters or persist
                    return
                
                # Update counters only if documents were successfully added
                self.document_count += len(documents)
                self._documents_since_persist += len(documents)
                
                # Batch persist: only persist periodically or when batch size reached
                current_time = time.time()
                should_persist = (
                    self._documents_since_persist >= self._persist_batch_size or
                    (current_time - self._last_persist_time) >= self._persist_interval
                )
                
                if should_persist:
                    await self._persist_with_retry()
                
                logger.info(f"[RAG] Successfully added {len(documents)} document chunks, total: {self.document_count} chunks")
                
            except Exception as e:
                logger.error(f"[RAG] Error adding webpage content ({url}): {e}")
                # Re-raise to let caller know it failed
                raise
    
    async def _persist_with_retry(self):
        """Persist vectorstore with retry mechanism"""
        if self.vectorstore is None:
            return
        
        max_retries = 3
        retry_delay = 0.5
        
        for attempt in range(max_retries):
            try:
                await asyncio.to_thread(self.vectorstore.persist)
                self._last_persist_time = time.time()
                self._documents_since_persist = 0
                self._pending_persist = False
                return
            except Exception as e:
                error_str = str(e).lower()
                if "compaction" in error_str or "metadata segment" in error_str:
                    if attempt < max_retries - 1:
                        wait_time = retry_delay * (2 ** attempt)
                        logger.warning(
                            f"[RAG] Persist compaction error (attempt {attempt + 1}/{max_retries}), "
                            f"retrying in {wait_time}s: {e}"
                        )
                        await asyncio.sleep(wait_time)
                        continue
                    else:
                        logger.error(f"[RAG] Persist failed after {max_retries} attempts: {e}")
                        # Mark as pending for next time
                        self._pending_persist = True
                        return
                else:
                    # Non-compaction error, log and return
                    logger.error(f"[RAG] Persist failed: {e}")
                    self._pending_persist = True
                    return
    
    async def force_persist(self):
        """Force persist immediately (useful at end of workflow)"""
        async with self._write_lock:
            await self._persist_with_retry()

    async def get_context_for_single_query(self, query: str, max_chars: int = 18000) -> str:
        """Get context for a single query"""
        if self.vectorstore is None:
            logger.warning("[RAG] Vector store is empty, cannot retrieve")
            return ""
        
        try:
            logger.info(f"[RAG] Retrieving query: {query[:50]}...")
            mmr_docs = await asyncio.to_thread(
                self.vectorstore.max_marginal_relevance_search,
                query,
                k=15,
                fetch_k=60,
                lambda_mult=0.5
            )

            # Build context
            context_parts = []
            total_chars = 0
            seen_urls = set()
            for doc in mmr_docs:
                source_url = doc.metadata.get("source_url", "unknown")
                content = doc.page_content
                if source_url not in seen_urls:
                    header = f"\n--- Source: {source_url} ---\n"
                    context_parts.append(header)
                    total_chars += len(header)
                    seen_urls.add(source_url)
                if total_chars + len(content) > max_chars:
                    remaining = max_chars - total_chars
                    if remaining > 100:
                        context_parts.append(content[:remaining] + "...[truncated]")
                    break
                context_parts.append(content + "\n")
                total_chars += len(content) + 1

            context = "".join(context_parts)
            logger.info(
                f"[RAG] Query retrieval completed: {len(context)} chars, "
                f"from {len(seen_urls)} different sources"
            )
            return context
        except Exception as e:
            logger.error(f"[RAG] Error retrieving query '{query}': {e}")
            return ""

    def get_statistics(self) -> Dict[str, Any]:
        """Get RAG statistics"""
        return {
            "document_count": self.document_count,
            "vectorstore_initialized": self.vectorstore is not None,
            "persist_directory": self.persist_directory,
        }
    
    def clear_collection(self):
        """Clear all documents from the collection without deleting the database"""
        try:
            if self.vectorstore is None:
                logger.warning("[RAG] Cannot clear collection: vectorstore is not initialized")
                return
            
            logger.info("[RAG] Clearing all documents from collection...")
            # Get all document IDs from the collection
            try:
                # Get all documents to retrieve their IDs
                all_docs = self.vectorstore.get()
                if all_docs and 'ids' in all_docs and len(all_docs['ids']) > 0:
                    # Delete all documents by their IDs
                    self.vectorstore.delete(ids=all_docs['ids'])
                    logger.info(f"[RAG] Deleted {len(all_docs['ids'])} documents from collection")
                else:
                    logger.info("[RAG] Collection is already empty")
                
                # Reset counters after successful deletion
                self.document_count = 0
                self._seen_hashes.clear()
                self._documents_since_persist = 0
                logger.info("[RAG] Collection cleared successfully")
                
            except Exception as e:
                logger.warning(f"[RAG] Error deleting documents, trying to delete and recreate collection: {e}")
                # Fallback: try to delete and recreate the collection
                try:
                    if hasattr(self.vectorstore, '_client') and self.vectorstore._client is not None:
                        # Delete the collection
                        self.vectorstore._client.delete_collection(name=self.collection_name)
                        logger.info(f"[RAG] Deleted collection: {self.collection_name}")
                    
                    # Recreate the vectorstore after deleting collection
                    self.vectorstore = Chroma(
                        collection_name=self.collection_name,
                        embedding_function=self.embeddings,
                        persist_directory=self.persist_directory
                    )
                    # Reset counters
                    self.document_count = 0
                    self._seen_hashes.clear()
                    self._documents_since_persist = 0
                    logger.info("[RAG] Collection recreated and cleared successfully")
                except Exception as e2:
                    logger.error(f"[RAG] Error deleting and recreating collection: {e2}")
                    raise
        except Exception as e:
            logger.error(f"[RAG] Error clearing collection: {e}")
            raise
    
    def close(self):
        """Close RAG Manager and release resources"""
        try:
            if self.vectorstore is not None:
                # Force persist before closing
                try:
                    # Use synchronous persist since we're in close()
                    self.vectorstore.persist()
                except Exception as e:
                    logger.warning(f"[RAG] Error during final persist: {e}")
                
                # Try to close the Chroma client if it has a close method
                try:
                    if hasattr(self.vectorstore, '_client') and self.vectorstore._client is not None:
                        if hasattr(self.vectorstore._client, 'close'):
                            self.vectorstore._client.close()
                        elif hasattr(self.vectorstore._client, '__exit__'):
                            # If it's a context manager, try to exit
                            self.vectorstore._client.__exit__(None, None, None)
                except Exception as e:
                    logger.debug(f"[RAG] Error closing Chroma client: {e}")
                
                # Clear the vectorstore reference
                self.vectorstore = None
                logger.info("[RAG] RAG Manager closed successfully")
        except Exception as e:
            logger.warning(f"[RAG] Error closing RAG Manager: {e}")
    
    async def aclose(self):
        """Async close RAG Manager and release resources"""
        try:
            if self.vectorstore is not None:
                # Force persist before closing
                async with self._write_lock:
                    await self._persist_with_retry()
                
                # Try to close the Chroma client if it has a close method
                try:
                    if hasattr(self.vectorstore, '_client') and self.vectorstore._client is not None:
                        if hasattr(self.vectorstore._client, 'close'):
                            await asyncio.to_thread(self.vectorstore._client.close)
                        elif hasattr(self.vectorstore._client, '__aexit__'):
                            # If it's an async context manager, try to exit
                            await self.vectorstore._client.__aexit__(None, None, None)
                except Exception as e:
                    logger.debug(f"[RAG] Error closing Chroma client: {e}")
                
                # Clear the vectorstore reference
                self.vectorstore = None
                logger.info("[RAG] RAG Manager closed successfully")
        except Exception as e:
            logger.warning(f"[RAG] Error closing RAG Manager: {e}")

