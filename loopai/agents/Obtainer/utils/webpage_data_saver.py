import os
import json
import asyncio
from datetime import datetime
from typing import Dict, Any, List, Optional
import aiosqlite

from loopai.logger import get_logger

logger = get_logger()


class WebPageDataSaver:
    """Save collected webpage data to JSONL and/or SQLite database"""
    
    def __init__(
        self,
        output_dir: str,
        jsonl_filename: Optional[str] = None,
        db_filename: Optional[str] = None,
    ):
        """
        Initialize WebPageDataSaver
        
        Args:
            output_dir: Output directory for files
            jsonl_filename: JSONL filename (e.g., "webpage_data.jsonl")
            db_filename: SQLite database filename (e.g., "webpage_data.db")
        """
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        
        self.jsonl_path = None
        if jsonl_filename:
            self.jsonl_path = os.path.join(output_dir, jsonl_filename)
        
        self.db_path = None
        if db_filename:
            self.db_path = os.path.join(output_dir, db_filename)
        
        self._jsonl_file = None
        self._db_initialized = False
    
    async def initialize_db(self):
        """Initialize SQLite database with table structure"""
        if not self.db_path:
            return
        
        if self._db_initialized:
            return
        
        try:
            async with aiosqlite.connect(self.db_path) as conn:
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS webpage_data (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        url TEXT NOT NULL,
                        title TEXT,
                        content TEXT,
                        structured_data TEXT,
                        timestamp TEXT NOT NULL,
                        source TEXT,
                        metadata TEXT,
                        UNIQUE(url, timestamp)
                    )
                """)
                
                await conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_url ON webpage_data(url)
                """)
                
                await conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_timestamp ON webpage_data(timestamp)
                """)
                
                await conn.commit()
                logger.info(f"[WebPageDataSaver] Database initialized: {self.db_path}")
                self._db_initialized = True
        except Exception as e:
            logger.error(f"[WebPageDataSaver] Error initializing database: {e}")
            raise
    
    async def save_webpage_data(
        self,
        url: str,
        title: str = "",
        content: str = "",
        structured_data: Optional[Dict[str, Any]] = None,
        source: str = "webpage_collect",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Save webpage data to JSONL and/or database
        
        Args:
            url: Webpage URL
            title: Page title
            content: Page content (text or HTML)
            structured_data: Structured data from Jina or other parsers
            source: Data source identifier
            metadata: Additional metadata
        
        Returns:
            True if saved successfully
        """
        timestamp = datetime.utcnow().isoformat() + "Z"
        
        data = {
            "url": url,
            "title": title,
            "content": content,
            "structured_data": structured_data or {},
            "timestamp": timestamp,
            "source": source,
            "metadata": metadata or {},
        }
        
        success = True
        
        # Save to JSONL
        if self.jsonl_path:
            try:
                # Open file in append mode
                mode = 'a' if os.path.exists(self.jsonl_path) else 'w'
                with open(self.jsonl_path, mode, encoding='utf-8') as f:
                    f.write(json.dumps(data, ensure_ascii=False) + "\n")
                logger.debug(f"[WebPageDataSaver] Saved to JSONL: {url}")
            except Exception as e:
                logger.error(f"[WebPageDataSaver] Error saving to JSONL: {e}")
                success = False
        
        # Save to database
        if self.db_path:
            try:
                await self.initialize_db()
                async with aiosqlite.connect(self.db_path) as conn:
                    await conn.execute("""
                        INSERT OR REPLACE INTO webpage_data 
                        (url, title, content, structured_data, timestamp, source, metadata)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (
                        url,
                        title,
                        content,
                        json.dumps(structured_data) if structured_data else None,
                        timestamp,
                        source,
                        json.dumps(metadata) if metadata else None,
                    ))
                    await conn.commit()
                logger.debug(f"[WebPageDataSaver] Saved to database: {url}")
            except Exception as e:
                logger.error(f"[WebPageDataSaver] Error saving to database: {e}")
                success = False
        
        return success
    
    async def save_batch(
        self,
        data_list: List[Dict[str, Any]],
    ) -> int:
        """
        Save multiple webpage data entries in batch
        
        Args:
            data_list: List of data dictionaries
        
        Returns:
            Number of successfully saved entries
        """
        saved_count = 0
        
        for data in data_list:
            success = await self.save_webpage_data(
                url=data.get("url", ""),
                title=data.get("title", ""),
                content=data.get("content", ""),
                structured_data=data.get("structured_data"),
                source=data.get("source", "webpage_collect"),
                metadata=data.get("metadata"),
            )
            if success:
                saved_count += 1
        
        logger.info(f"[WebPageDataSaver] Batch saved: {saved_count}/{len(data_list)} entries")
        return saved_count
    
    async def get_saved_count(self) -> Dict[str, int]:
        """
        Get count of saved entries
        
        Returns:
            Dictionary with counts from JSONL and database
        """
        counts = {"jsonl": 0, "database": 0}
        
        # Count JSONL lines
        if self.jsonl_path and os.path.exists(self.jsonl_path):
            try:
                with open(self.jsonl_path, 'r', encoding='utf-8') as f:
                    counts["jsonl"] = sum(1 for line in f if line.strip())
            except Exception as e:
                logger.warning(f"[WebPageDataSaver] Error counting JSONL: {e}")
        
        # Count database entries
        if self.db_path and os.path.exists(self.db_path):
            try:
                await self.initialize_db()
                async with aiosqlite.connect(self.db_path) as conn:
                    async with conn.execute("SELECT COUNT(*) FROM webpage_data") as cursor:
                        row = await cursor.fetchone()
                        counts["database"] = row[0] if row else 0
            except Exception as e:
                logger.warning(f"[WebPageDataSaver] Error counting database: {e}")
        
        return counts
    
    def get_jsonl_path(self) -> Optional[str]:
        """Get JSONL file path"""
        return self.jsonl_path
    
    def get_db_path(self) -> Optional[str]:
        """Get database file path"""
        return self.db_path








