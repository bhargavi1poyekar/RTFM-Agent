# src/rtfm_agent/ingestion/base.py
"""
Base classes for documentation ingestion pipeline.
This provides a foundation for scraping any technical documentation.
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from pathlib import Path
import json
import hashlib
from datetime import datetime
from dataclasses import dataclass, asdict
import time

from config.logging import logger


@dataclass
class DocumentChunk:
    """
    Represents a chunk of documentation with metadata.
    This is the core data unit in our ingestion pipeline.
    """
    chunk_id: str
    content: str
    source_url: str
    title: str
    section: str
    metadata: Dict[str, Any]
    code_blocks: List[str]
    created_at: str
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DocumentChunk":
        """Create from dictionary."""
        return cls(**data)
    
    def generate_id(self, content: str) -> str:
        """Generate a unique ID based on content."""
        return hashlib.md5(
            f"{self.source_url}:{self.section}:{content[:100]}".encode()
        ).hexdigest()


class BaseScraper(ABC):
    """
    Abstract base class for documentation scrapers.
    All scraper implementations must inherit from this.
    """
    
    def __init__(
            self, 
            base_url: str, 
            output_dir: str = "data/raw", 
            requests_per_second: float = 2.0, 
        ):
        """
        Initialize the scraper.
        
        Args:
            base_url: Root URL of documentation
            output_dir: Directory to store scraped content
        """
        self.base_url = base_url
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.visited_urls = set()
        self.chunks: List[DocumentChunk] = []

        # Rate limiting
        self.requests_per_second = requests_per_second
        self.min_request_interval = 1.0 / requests_per_second
        self.last_request_time = 0.0

    def _rate_limit(self) -> None:
        """Enforce rate limiting between requests."""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.min_request_interval:
            time.sleep(self.min_request_interval - elapsed)
        self.last_request_time = time.time()
        
    @abstractmethod
    def get_page_content(self, url: str) -> Dict[str, Any]:
        """
        Fetch and parse content from a URL.
        
        Args:
            url: Page URL to scrape
            
        Returns:
            Dictionary with title, content, code_blocks, etc.
        """
        pass
    
    @abstractmethod
    def get_links(self, url: str) -> List[str]:
        """
        Extract links from a page.
        
        Args:
            url: Page URL to extract links from
            
        Returns:
            List of URLs to visit
        """
        pass
    
    @abstractmethod
    def clean_content(self, html_content: str) -> str:
        """
        Clean HTML content to plain text.
        
        Args:
            html_content: Raw HTML from page
            
        Returns:
            Cleaned text content
        """
        pass
    
    @abstractmethod
    def extract_code_blocks(self, html_content: str) -> List[str]:
        """
        Extract code blocks from HTML.
        
        Args:
            html_content: Raw HTML from page
            
        Returns:
            List of code strings
        """
        pass
    
    @abstractmethod
    def chunk_content(self, content: str, metadata: Dict[str, Any]) -> List[DocumentChunk]:
        """
        Split content into chunks.
        
        Args:
            content: Full page content
            metadata: Page metadata
            
        Returns:
            List of DocumentChunk objects
        """
        pass
    
    def scrape(self, max_pages: int = 500, seed_urls: Optional[List[str]] = None,) -> List[DocumentChunk]:
        """
        Main scraping orchestrator.
        
        Args:
            max_pages: Maximum pages to scrape
            
        Returns:
            List of DocumentChunk objects
        """
        seeds = seed_urls or [self.base_url]
        
        logger.info(f"Starting scrape with {len(seeds)} seed(s)")
        logger.info(f"Max pages: {max_pages}")
        
        urls_to_visit = self.prioritize_urls(list(seeds))
        pages_processed = 0
        
        while urls_to_visit and pages_processed < max_pages:
            current_url = urls_to_visit.pop(0)
            
            if current_url in self.visited_urls:
                continue
            
            pages_processed += 1
            logger.info(
                f"[{pages_processed}/{max_pages}] Scraping: "
                f"{current_url.replace('https://boto3.amazonaws.com/v1/documentation/api/latest/', '')}"
            )
            
            try:
                page_data = self.get_page_content(current_url)
                
                if not page_data:
                    logger.warning(f"No data from {current_url}")
                    self.visited_urls.add(current_url)
                    continue
                
                # Get links
                new_links = self.get_links(current_url)
                for link in new_links:
                    if link not in self.visited_urls and link not in urls_to_visit:
                        urls_to_visit.append(link)

                if pages_processed % 10 == 0:
                    urls_to_visit = self.prioritize_urls(urls_to_visit)
                
                # Chunk
                metadata = {
                    "url": current_url,
                    "title": page_data.get("title", ""),
                    "scraped_at": datetime.now().isoformat(),
                    "chunk_size": len(page_data.get("content", "")),
                }
                
                page_chunks = self.chunk_content(
                    page_data.get("content", ""),
                    metadata
                )
                
                self.chunks.extend(page_chunks)
                self.visited_urls.add(current_url)
                
                logger.info(
                    f"  → {len(page_chunks)} chunks | "
                    f"{len(urls_to_visit)} URLs queued"
                )
                
            except Exception as e:
                logger.error(f"Error scraping {current_url}: {e}")
                self.visited_urls.add(current_url)
                continue
        
        logger.info(f"Scraping complete. Total chunks: {len(self.chunks)}")
        self._save_chunks()
        return self.chunks
    
    def _save_chunks(self) -> None:
        """Save chunks to JSON file."""
        output_file = self.output_dir / "chunks.json"
        
        chunks_data = [chunk.to_dict() for chunk in self.chunks]
        
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(chunks_data, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Saved {len(self.chunks)} chunks to {output_file}")
    
    def load_chunks(self, file_path: Optional[str] = None) -> List[DocumentChunk]:
        """Load chunks from JSON file."""
        if file_path is None:
            file_path = self.output_dir / "chunks.json"
        
        with open(file_path, "r", encoding="utf-8") as f:
            chunks_data = json.load(f)
        
        self.chunks = [DocumentChunk.from_dict(data) for data in chunks_data]
        logger.info(f"Loaded {len(self.chunks)} chunks from {file_path}")
        return self.chunks