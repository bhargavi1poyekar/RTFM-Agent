# src/rtfm_agent/ingestion/boto3_scraper.py
"""
Boto3 documentation scraper implementation.
"""
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin, urlparse
import re
import hashlib

import requests
from bs4 import BeautifulSoup
from html2text import HTML2Text

from src.rtfm_agent.ingestion.base import BaseScraper, DocumentChunk
from config.logging import logger


class Boto3Scraper(BaseScraper):
    """
    Scraper for AWS Boto3 documentation.
    """

    REFERENCE_SEEDS = [
        # Main reference index
        "https://boto3.amazonaws.com/v1/documentation/api/latest/reference/index.html",
        
        # Top 10 most-used AWS services (covers 90% of use cases)
        "https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3.html",
        "https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/ec2.html",
        "https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/iam.html",
        "https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/lambda.html",
        "https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/dynamodb.html",
        "https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/sqs.html",
        "https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/sns.html",
        "https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/cloudwatch.html",
        "https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/cloudformation.html",
        "https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/rds.html",
    ]
    
    def __init__(
        self,
        base_url: str = "https://boto3.amazonaws.com/v1/documentation/api/latest/index.html",
        output_dir: str = "data/raw",
        exclude_patterns: Optional[List[str]] = None,
        requests_per_second: float = 2.0,
    ):
        """
        Initialize Boto3 scraper.
        
        Args:
            base_url: Root URL of Boto3 docs
            output_dir: Directory to store scraped content
            exclude_patterns: URL patterns to exclude
        """
        super().__init__(base_url, output_dir, requests_per_second=requests_per_second,)
        
        self.exclude_patterns = exclude_patterns or [
            "/search",
            "/_static",
            "/_modules",
            "/_sources",
            "/genindex",
            "/py-modindex",
            "/changelog",
        ]
        
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "RTFM-Agent/1.0 (Documentation Scraper; +https://github.com/yourusername/rtfm-agent)"
        })

    def get_all_seeds(self) -> List[str]:
        """
        Return list of seed URLs for complete coverage.
        Includes main index + reference pages for popular services.
        """
        return [self.base_url] + self.REFERENCE_SEEDS

    def prioritize_urls(self, urls: List[str]) -> List[str]:
        """
        Sort URLs by priority so we scrape the most valuable pages first.
        
        Priority (highest to lowest):
        1. Reference service pages (API methods) - TOP PRIORITY
        2. Reference sub-pages (method details)
        3. Guide pages (tutorials)
        4. Everything else
        """
        def priority_score(url: str) -> int:
            if "/reference/services/" in url:
                # Higher score for shorter paths (main service page)
                depth = url.count("/")
                return 1000 - depth
            if "/reference/" in url:
                return 800
            if "/guide/" in url:
                # Prefer shorter guide paths
                depth = url.count("/")
                return 500 - depth
            return 100
        
        return sorted(urls, key=priority_score, reverse=True)
        
    def get_page_content(self, url: str) -> Dict[str, Any]:
        self._rate_limit()  
        """
        Fetch and parse Boto3 documentation page.
        """
        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, "lxml")
            
            # Extract title
            title_elem = soup.find("title")
            title = title_elem.get_text(strip=True) if title_elem else ""
            
            # Extract main content
            content_elem = soup.find("div", {"class": "body"})
            if not content_elem:
                content_elem = soup.find("main")
            if not content_elem:
                content_elem = soup.find("article")
            
            if content_elem:
                # Remove navigation, headers, footers
                for element in content_elem.find_all(["nav", "footer", "aside"]):
                    element.decompose()
                
                content_html = str(content_elem)
            else:
                content_html = str(soup)
            
            # Clean content
            clean_text = self.clean_content(content_html)
            
            # Extract code blocks
            code_blocks = self.extract_code_blocks(content_html)
            
            # Get section hierarchy
            section = self._get_section(soup, url)
            
            return {
                "title": title,
                "content": clean_text,
                "html": content_html,
                "code_blocks": code_blocks,
                "section": section,
                "url": url,
            }
            
        except requests.RequestException as e:
            logger.error(f"Error fetching {url}: {e}")
            return {}
    
    def get_links(self, url: str) -> List[str]:
        self._rate_limit()  
        """
        Extract all internal documentation links from a Boto3 page.
        """
        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, "lxml")
            
            links = []
            for anchor in soup.find_all("a", href=True):
                href = anchor["href"]
                
                # Skip anchors and javascript
                if href.startswith("#") or href.startswith("javascript:"):
                    continue
                
                # Build absolute URL
                absolute_url = urljoin(url, href)
                
                # Parse URL
                parsed = urlparse(absolute_url)
                
                # Only keep docs.boto3.amazonaws.com links
                if "boto3.amazonaws.com" not in parsed.netloc:
                    continue
                
                # Exclude patterns
                if any(pattern in absolute_url for pattern in self.exclude_patterns):
                    continue
                
                # Remove fragment
                clean_url = urljoin(absolute_url, urlparse(absolute_url).path)
                
                if clean_url not in self.visited_urls:
                    links.append(clean_url)
            
            return links[:100]  # Limit to 100 per page
            
        except requests.RequestException as e:
            logger.error(f"Error getting links from {url}: {e}")
            return []
    
    def clean_content(self, html_content: str) -> str:
        """
        Clean HTML to markdown, preserving code blocks reliably.
        
        Strategy:
        1. Extract all code blocks FIRST (before any text conversion)
        2. Replace them with safe unique placeholders
        3. Convert remaining HTML to markdown
        4. Restore code blocks in their original positions
        """
        soup = BeautifulSoup(html_content, "lxml")
        
        # Storage: placeholder -> markdown code block
        code_storage = {}
        
        # === STEP 1: Find all code containers ===
        # Priority order: prefer outer container to avoid double-capture
        code_containers = []
        captured_ids = set()
        
        # Pattern A: Sphinx's outer wrapper (highlight-default, highlight-python, etc.)
        for div in soup.find_all("div", class_=re.compile(r"^highlight(-\w+)?$")):
            # Skip if parent is also a highlight div (avoid nesting)
            if div.find_parent("div", class_=re.compile(r"^highlight")):
                continue
            code_containers.append(div)
            captured_ids.add(id(div))
        
        # Pattern B: Any remaining <pre> not already captured
        for pre in soup.find_all("pre"):
            # Skip if inside an already-captured container
            if any(id(p) in captured_ids for p in pre.parents):
                continue
            code_containers.append(pre)
            captured_ids.add(id(pre))
        
        logger.debug(f"Found {len(code_containers)} code containers")
        
        # === STEP 2: Replace each container with a safe placeholder ===
        for i, container in enumerate(code_containers):
            # Get raw text from the container
            code_text = container.get_text()
            
            # Skip empty/tiny code
            if not code_text or len(code_text.strip()) < 10:
                container.decompose()  # Remove it entirely
                continue
            
            # Detect language
            language = self._detect_language_from_container(container, code_text)
            
            # Create markdown code block
            markdown_code = f"\n\n```{language}\n{code_text.strip()}\n```\n\n"
            
            # Use unique placeholder that survives html2text
            # Format: __CODEBLOCK_N__ where N is the index
            placeholder = f"__CODEBLOCK_{i}__"
            code_storage[placeholder] = markdown_code
            
            # Replace container with plain text placeholder
            container.replace_with(placeholder)
        
        # === STEP 3: Convert remaining HTML to markdown ===
        converter = HTML2Text()
        converter.ignore_links = False
        converter.ignore_images = True
        converter.ignore_tables = False
        converter.ignore_emphasis = False
        converter.body_width = 0  # No line wrapping
        
        text = converter.handle(str(soup))
        
        # === STEP 4: Restore code blocks ===
        for placeholder, markdown_code in code_storage.items():
            text = text.replace(placeholder, markdown_code)
        
        # === STEP 5: Cleanup ===
        # Remove excessive blank lines
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = text.strip()
        
        return text

    def _detect_language_from_container(self, container, code_text: str) -> str:
        """
        Detect programming language from container classes or code content.
        """
        # Check container classes (real Boto3 uses: highlight-default, highlight-python)
        if hasattr(container, "get"):
            classes = " ".join(container.get("class", []))
            classes_lower = classes.lower()
            
            # Language hints in class names
            language_map = {
                "python": ["python", "py", "default"],  # "default" in Sphinx = python
                "bash": ["bash", "shell", "console"],
                "json": ["json"],
                "yaml": ["yaml"],
                "xml": ["xml"],
                "javascript": ["javascript", "js"],
                "java": ["java"],
                "go": ["go", "golang"],
            }
            
            for lang, hints in language_map.items():
                if any(hint in classes_lower for hint in hints):
                    return lang
        
        # Fallback: Heuristic based on content
        code_lower = code_text.lower()
        
        if any(kw in code_lower for kw in ["import boto3", "boto3.", "client.", "def ", "print("]):
            return "python"
        if code_lower.strip().startswith(("{", "[")):
            return "json"
        if any(kw in code_lower for kw in ["aws ", "pip ", "export ", "$ "]):
            return "bash"
        
        return "python"  # Default for Boto3 docs

    def _extract_section_hierarchy(self, content: str) -> List[Dict[str, str]]:
        """
        Extract sections with proper hierarchy.
        Merges sub-headers (###) into their parent sections (##).
        """
        lines = content.split("\n")
        sections = []
        current_h2 = "Overview"
        current_content = []
        
        # Skip-list for section names that shouldn't stand alone
        skip_sections = {"note", "warning", "tip", "important", "see also"}
        
        for line in lines:
            # Detect ## headers (main sections)
            h2_match = re.match(r'^##\s+(.+?)(?:¶)?$', line)
            h3_match = re.match(r'^###\s+(.+?)(?:¶)?$', line)
            
            if h2_match:
                # Save previous section
                if current_content:
                    sections.append({
                        "header": current_h2,
                        "content": "\n".join(current_content).strip()
                    })
                
                current_h2 = h2_match.group(1).strip()
                current_content = []
            
            elif h3_match:
                header_text = h3_match.group(1).strip().lower()
                
                # If it's a "Note/Warning/Tip", keep it in current section
                if header_text in skip_sections:
                    current_content.append(line)
                else:
                    # It's a real subsection - include as part of parent
                    current_content.append(f"\n**{h3_match.group(1).strip()}**\n")
            
            else:
                current_content.append(line)
        
        # Don't forget the last section
        if current_content:
            sections.append({
                "header": current_h2,
                "content": "\n".join(current_content).strip()
            })
        
        return sections
    
    def extract_code_blocks(self, html_content: str) -> List[str]:
        """
        Extract code blocks as markdown-formatted strings.
        Uses same container detection logic as clean_content().
        """
        soup = BeautifulSoup(html_content, "lxml")
        code_blocks = []
        
        # Find outer containers (avoid nesting)
        code_containers = []
        
        for div in soup.find_all("div", class_=re.compile(r"^highlight(-\w+)?$")):
            if div.find_parent("div", class_=re.compile(r"^highlight")):
                continue  # Skip inner, keep outer
            code_containers.append(div)
        
        captured_ids = {id(c) for c in code_containers}
        
        for pre in soup.find_all("pre"):
            if any(id(p) in captured_ids for p in pre.parents):
                continue
            code_containers.append(pre)
        
        # Extract each container
        for container in code_containers:
            code_text = container.get_text()
            if not code_text or len(code_text.strip()) < 10:
                continue
            
            language = self._detect_language_from_container(container, code_text)
            code_blocks.append(f"```{language}\n{code_text.strip()}\n```")
        
        # Deduplicate
        seen = set()
        unique_blocks = []
        for block in code_blocks:
            fingerprint = block[:150]
            if fingerprint not in seen:
                seen.add(fingerprint)
                unique_blocks.append(block)
        
        return unique_blocks
        

    def chunk_content(
        self,
        content: str,
        metadata: Dict[str, Any]
    ) -> List[DocumentChunk]:
        """
        Split content into semantically meaningful chunks.
        
        Strategy:
        1. Extract sections using hierarchy-aware parser
        2. Merge small sections, split large ones
        3. Preserve code blocks intact
        """
        if not content or len(content.strip()) < 50:
            return []
        
        min_size = 300
        max_size = 1500
        
        # Step 1: Extract hierarchical sections
        sections = self._extract_section_hierarchy(content)
        
        # Step 2: Merge small sections into logical chunks
        chunks = []
        buffer_content = ""
        buffer_header = ""
        buffer_code = []
        
        for section in sections:
            section_content = section["content"]
            section_header = section["header"]
            
            if not section_content:
                continue
            
            # Extract code blocks from section
            code_pattern = r'```(\w*)\n(.*?)```'
            code_matches = re.findall(code_pattern, section_content, re.DOTALL)
            section_code = [f"```{lang}\n{code}\n```" for lang, code in code_matches]
            
            # Case 1: Section too large - flush buffer, then split section
            if len(section_content) > max_size:
                if buffer_content:
                    chunks.append(self._create_chunk(
                        buffer_content, metadata, buffer_header, buffer_code
                    ))
                    buffer_content = ""
                    buffer_code = []
                
                # Split large section
                sub_chunks = self._split_large_section(
                    section_content, 
                    max_size=max_size)
                
                for sub_content in sub_chunks:
                    sub_code = re.findall(code_pattern, sub_content, re.DOTALL)
                    sub_code_formatted = [f"```{lang}\n{code}\n```" for lang, code in sub_code]
                    
                    if len(sub_content) >= min_size:
                        chunks.append(self._create_chunk(
                            sub_content, metadata, section_header, sub_code_formatted
                        ))
            
            # Case 2: Section is small - add to buffer
            elif len(buffer_content) + len(section_content) <= max_size:
                if not buffer_content:
                    buffer_header = section_header
                buffer_content += f"\n\n## {section_header}\n\n{section_content}"
                buffer_code.extend(section_code)
            
            # Case 3: Adding section would exceed max - flush and start new
            else:
                if buffer_content:
                    chunks.append(self._create_chunk(
                        buffer_content, metadata, buffer_header, buffer_code
                    ))
                
                buffer_content = f"## {section_header}\n\n{section_content}"
                buffer_header = section_header
                buffer_code = section_code
        
        # Flush remaining buffer
        if buffer_content and len(buffer_content) >= min_size:
            chunks.append(self._create_chunk(
                buffer_content, metadata, buffer_header, buffer_code
            ))
        
        return chunks

    def _split_large_section(
        self,
        content: str,
        header: str = "",
        max_size: int = 1500
    ) -> List[str]:
        """Split a large section into smaller chunks preserving structure."""
        if len(content) <= max_size:
            return [content]
        
        chunks = []
        
        # Try to split by code blocks first (preserve them intact)
        parts = re.split(r'(```[\w]*\s*\n.*?```)', content, flags=re.DOTALL)
        
        current = ""
        for part in parts:
            if len(current) + len(part) > max_size and current:
                chunks.append(current.strip())
                current = part
            else:
                current += part
        
        if current.strip():
            chunks.append(current.strip())
        
        # If still too large, split by paragraphs
        final_chunks = []
        for chunk in chunks:
            if len(chunk) <= max_size:
                final_chunks.append(chunk)
            else:
                # Split by double newlines
                paragraphs = chunk.split("\n\n")
                current = ""
                for para in paragraphs:
                    if len(current) + len(para) > max_size and current:
                        final_chunks.append(current.strip())
                        current = para + "\n\n"
                    else:
                        current += para + "\n\n"
                
                if current.strip():
                    final_chunks.append(current.strip())
        
        return final_chunks
    
    def _create_chunk(
        self,
        content: str,
        metadata: Dict[str, Any],
        section: str,
        code_blocks: List[str]
    ) -> DocumentChunk:
        """Create a DocumentChunk from content."""
        chunk_id = hashlib.md5(
            f"{metadata.get('url')}:{section}:{content[:100]}".encode()
        ).hexdigest()
        
        return DocumentChunk(
            chunk_id=chunk_id,
            content=content.strip(),
            source_url=metadata.get("url", ""),
            title=metadata.get("title", ""),
            section=section,
            metadata=metadata,
            code_blocks=code_blocks,
            created_at=metadata.get("scraped_at", ""),
        )
    
    def _fixed_size_chunking(
        self,
        content: str,
        metadata: Dict[str, Any]
    ) -> List[DocumentChunk]:
        """Fallback: Chunk by fixed size with overlap."""
        chunks = []
        chunk_size = 1000
        overlap = 100
        
        for i in range(0, len(content), chunk_size - overlap):
            chunk_content = content[i:i + chunk_size]
            chunk_id = hashlib.md5(
                f"{metadata.get('url')}:{i}:{chunk_content[:100]}".encode()
            ).hexdigest()
            
            chunks.append(DocumentChunk(
                chunk_id=chunk_id,
                content=chunk_content,
                source_url=metadata.get("url", ""),
                title=metadata.get("title", ""),
                section=f"chunk_{i//chunk_size}",
                metadata=metadata,
                code_blocks=[],
                created_at=metadata.get("scraped_at", ""),
            ))
        
        return chunks
    
    def _get_section(self, soup: BeautifulSoup, url: str) -> str:
        """Extract section hierarchy from the page."""
        # Try to get breadcrumb
        breadcrumb = soup.find("ul", {"class": "breadcrumb"})
        if breadcrumb:
            items = breadcrumb.find_all("li")
            sections = [item.get_text(strip=True) for item in items]
            return " > ".join(sections)
        
        # Try to get from URL
        path_parts = urlparse(url).path.strip("/").split("/")
        if path_parts:
            # Remove "latest" or version numbers
            clean_parts = [p for p in path_parts if not p.isdigit() and p != "latest"]
            if clean_parts:
                return " > ".join(clean_parts)
        
        return "Documentation"