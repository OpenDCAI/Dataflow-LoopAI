"""
RAG Manager for storing and retrieving web content
"""
import os
import re
import shutil
import hashlib
import asyncio
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
        """
        Initialize RAG Manager
        
        Args:
            api_base_url: API base URL for embeddings
            api_key: API key for embeddings
            embed_model: Embedding model name
            persist_directory: Directory to persist vector store
            reset: Whether to reset the vector store
            collection_name: Collection name for vector store
        """
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

    async def add_webpage_content(
        self, url: str, text_content: str, metadata: Optional[Dict[str, Any]] = None
    ):
        """
        Add webpage content to RAG
        
        Args:
            url: Source URL
            text_content: Text content of the webpage
            metadata: Additional metadata
        """
        if not text_content or len(text_content.strip()) < 50:
            logger.info(f"[RAG] Skipping webpage with too short content: {url}")
            return
        
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
            
            if self.vectorstore is None:
                # Fallback: create if initialization failed
                self.vectorstore = await asyncio.to_thread(
                    Chroma.from_documents,
                    documents=documents,
                    embedding=self.embeddings,
                    persist_directory=self.persist_directory,
                )
            else:
                await asyncio.to_thread(self.vectorstore.add_documents, documents)
            
            # Persist immediately
            try:
                await asyncio.to_thread(self.vectorstore.persist)
            except Exception as e:
                logger.error(f"[RAG] Persistence failed: {e}")
            
            self.document_count += len(documents)
            logger.info(f"[RAG] Successfully added {len(documents)} document chunks, total: {self.document_count} chunks")
        except Exception as e:
            logger.error(f"[RAG] Error adding webpage content ({url}): {e}")

    async def get_context_for_single_query(self, query: str, max_chars: int = 18000) -> str:
        """
        Get context for a single query
        
        Args:
            query: Search query
            max_chars: Maximum characters to return
            
        Returns:
            Context string
        """
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

