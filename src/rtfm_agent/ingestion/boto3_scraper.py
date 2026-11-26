# src/rtfm_agent/ingestion/boto3_scraper.py
"""
Boto3 documentation scraper implementation.
"""
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin, urlparse
import re

import requests
from bs4 import BeautifulSoup
from html2text import HTML2Text

from src.rtfm_agent.ingestion.base import BaseScraper, DocumentChunk, make_chunk_id
from config.logging import logger
from config.settings import settings


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
        min_chunk_size: Optional[int] = None,
        max_chunk_size: Optional[int] = None,
    ):
        """
        Initialize Boto3 scraper.

        Args:
            base_url: Root URL of Boto3 docs
            output_dir: Directory to store scraped content
            exclude_patterns: URL patterns to exclude
            min_chunk_size: Drop chunks shorter than this (chars). Default: settings.
            max_chunk_size: Hard cap on chunk length (chars). Default: settings.
        """
        super().__init__(base_url, output_dir, requests_per_second=requests_per_second,)

        self.min_chunk_size = min_chunk_size or settings.CHUNK_MIN_SIZE
        self.max_chunk_size = max_chunk_size or settings.CHUNK_SIZE

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

        # Single-page cache so get_page_content() and get_links() on the same
        # URL share one HTTP request. Stores raw bytes (not a parsed soup)
        # because get_page_content() mutates its soup (removes <nav>), which
        # would hide links from get_links().
        self._cached_url: Optional[str] = None
        self._cached_html: Optional[bytes] = None

    def _fetch_html(self, url: str) -> bytes:
        """
        Download a page (rate-limited), reusing the last response if the
        same URL is requested again. Raises requests.RequestException on failure.
        """
        if url == self._cached_url and self._cached_html is not None:
            return self._cached_html

        self._rate_limit()
        response = self.session.get(url, timeout=30)
        response.raise_for_status()

        self._cached_url = url
        self._cached_html = response.content
        return response.content

    def get_all_seeds(self) -> List[str]:
        """
        Return list of seed URLs for complete coverage.
        Includes main index + reference pages for popular services.
        """
        return [self.base_url] + self.REFERENCE_SEEDS

    @property
    def focus_services(self) -> set:
        """Service names from REFERENCE_SEEDS, e.g. {"s3", "ec2", ...}."""
        return {
            seed.rsplit("/", 1)[1][: -len(".html")]
            for seed in self.REFERENCE_SEEDS
            if "/reference/services/" in seed
        }

    @staticmethod
    def _service_of(url: str) -> Optional[str]:
        """'.../reference/services/s3/client/put_object.html' -> 's3'."""
        marker = "/reference/services/"
        if marker not in url:
            return None
        rest = url.split(marker, 1)[1]
        return rest.split("/", 1)[0].removesuffix(".html")

    def _priority_tier(self, url: str) -> int:
        """
        Higher = scraped sooner.
          5  focus service landing page   (reference/services/s3.html)
          4  focus service client/resource page (s3/client/put_object.html)
          3  focus service paginator/waiter page (boilerplate-heavy)
          2  guide pages (tutorials)
          1  reference index pages
          0  everything else, incl. the ~400 non-focus services
        """
        service = self._service_of(url)
        if service in self.focus_services:
            if url.endswith(f"/services/{service}.html"):
                return 5
            if "/paginator/" in url or "/waiter/" in url:
                return 3
            return 4
        if "/guide/" in url:
            return 2
        if "/reference/" in url and service is None:
            return 1
        return 0

    def prioritize_urls(self, urls: List[str]) -> List[str]:
        """
        Sort URLs so the most valuable pages are scraped first.

        1. By tier (see _priority_tier): pages of the focus services first.
        2. Within a tier, round-robin across services (S3, EC2, IAM, S3,
           EC2, IAM, ...) so a big service like EC2 (1,000+ pages) can't
           use up the whole page budget before smaller ones get a turn.
        3. Otherwise keep discovery order (sort is stable).
        """
        seen: Dict[Any, int] = {}  # (tier, service) -> URLs seen so far
        keyed = []
        for url in urls:
            tier = self._priority_tier(url)
            group = (tier, self._service_of(url))
            turn = seen.get(group, 0)
            seen[group] = turn + 1
            keyed.append((-tier, turn, url))

        keyed.sort(key=lambda k: (k[0], k[1]))
        return [url for _, _, url in keyed]
        
    def get_page_content(self, url: str) -> Dict[str, Any]:
        """
        Fetch and parse Boto3 documentation page.
        """
        try:
            soup = BeautifulSoup(self._fetch_html(url), "lxml")
            
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
        """
        Extract all internal documentation links from a Boto3 page.
        """
        try:
            soup = BeautifulSoup(self._fetch_html(url), "lxml")
            
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
            
            # No cap: on service pages the first ~520 links are the sidebar
            # (other services) and the page's own method links come after,
            # so a cap like links[:100] made the crawler never go deeper.
            return list(dict.fromkeys(links))  # dedupe, keep order
            
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
        2. Merge small sections, split large ones (respecting size limits strictly)
        3. Filter out chunks that are too small
        4. Deduplicate chunks within a page
        """
        if not content or len(content.strip()) < 50:
            return []
        
        # Size limits come from settings (see CHUNK_SIZE comment there)
        min_size = self.min_chunk_size
        max_size = self.max_chunk_size

        # Step 1: Extract sections
        sections = self._extract_section_hierarchy(content)
        
        # Step 2: Build chunks by merging sections
        chunks = []
        buffer_content = ""
        buffer_header = ""
        buffer_code = []
        
        def flush_buffer():
            """Helper to flush the current buffer as a chunk."""
            nonlocal buffer_content, buffer_header, buffer_code
            if buffer_content and len(buffer_content.strip()) >= min_size:
                chunks.append(self._create_chunk(
                    buffer_content, metadata, buffer_header, buffer_code
                ))
            buffer_content = ""
            buffer_code = []
        
        for section in sections:
            section_content = section["content"].strip()
            section_header = section["header"]
            
            if not section_content:
                continue
            
            # Extract code blocks
            code_pattern = r'```(\w*)\n(.*?)```'
            code_matches = re.findall(code_pattern, section_content, re.DOTALL)
            section_code = [f"```{lang}\n{code}\n```" for lang, code in code_matches]
            
            # Exact text added when a section is prefixed with its heading
            header_block = f"## {section_header}\n\n"

            # Case 1: Section (with its heading) too large - flush, then split
            if len(header_block) + len(section_content) > max_size:
                flush_buffer()
                sub_chunks = self._split_large_section(section_content, max_size)
                for sub_content in sub_chunks:
                    sub_content = sub_content.strip()
                    if len(sub_content) < min_size:
                        continue
                    sub_code = re.findall(code_pattern, sub_content, re.DOTALL)
                    sub_code_formatted = [f"```{lang}\n{code}\n```" for lang, code in sub_code]
                    chunks.append(self._create_chunk(
                        sub_content, metadata, section_header, sub_code_formatted
                    ))
            
            # Case 2: Fits in buffer (measured with the real separator length)
            # (an empty buffer always fits: Case 1 caught anything too big)
            elif not buffer_content or (
                len(buffer_content) + 2 + len(header_block) + len(section_content)
                <= max_size
            ):
                if not buffer_content:
                    buffer_header = section_header
                else:
                    buffer_content += f"\n\n{header_block}"
                buffer_content += section_content
                buffer_code.extend(section_code)

            # Case 3: Doesn't fit - flush buffer, start fresh
            # (fits: Case 1 already guarantees heading + content <= max_size)
            else:
                flush_buffer()
                buffer_content = f"{header_block}{section_content}"
                buffer_header = section_header
                buffer_code = section_code
        
        # Final flush
        flush_buffer()
        
        # 👇 Deduplicate chunks
        chunks = self._deduplicate_chunks(chunks)
        
        return chunks

    def _deduplicate_chunks(
        self, chunks: List[DocumentChunk]
    ) -> List[DocumentChunk]:
        """
        Remove duplicate chunks (same whitespace-normalised content).
        Full content, not a prefix: different sections often share the same
        opening boilerplate, and prefix matching dropped real content.
        """
        seen = set()
        unique = []

        for chunk in chunks:
            fingerprint = " ".join(chunk.content.split())
            if fingerprint in seen:
                continue

            seen.add(fingerprint)
            unique.append(chunk)
        
        return unique

    def _split_large_section(
        self,
        content: str,
        max_size: int = 2000,
    ) -> List[str]:
        """
        Split a large section into smaller chunks, strictly respecting max_size.
        
        Multi-level strategy:
        1. Split by code blocks (preserve them)
        2. Split by markdown headers
        3. Split by double newlines (paragraphs)
        4. Split by single newlines (lines)
        5. Hard-split by character count (final fallback)
        """
        if len(content) <= max_size:
            return [content]
        
        # Strategy 1: Split by code blocks
        code_pattern = r'(```[\w]*\s*\n.*?```)'
        parts = re.split(code_pattern, content, flags=re.DOTALL)
        
        # Merge parts into size-respecting chunks
        chunks = []
        current = ""
        
        for part in parts:
            # If part itself is too big, we need to split it first
            if len(part) > max_size:
                # Flush current
                if current.strip():
                    chunks.append(current.strip())
                    current = ""
                
                # Split the oversized part
                sub_parts = self._split_oversized_part(part, max_size)
                # All but last go directly into chunks
                for sp in sub_parts[:-1]:
                    if sp.strip():
                        chunks.append(sp.strip())
                # Last one might fit with next parts
                current = sub_parts[-1] if sub_parts else ""
                continue
            
            # Normal case: try to add to current
            if len(current) + len(part) > max_size:
                if current.strip():
                    chunks.append(current.strip())
                current = part
            else:
                current += part
        
        if current.strip():
            chunks.append(current.strip())
        
        # FINAL SAFETY: Verify no chunk exceeds max_size
        verified_chunks = []
        for chunk in chunks:
            if len(chunk) <= max_size:
                verified_chunks.append(chunk)
            else:
                # Emergency hard-split (e.g. a single sentence > max_size)
                for i in range(0, len(chunk), max_size):
                    verified_chunks.append(chunk[i:i + max_size])
        
        return verified_chunks


    def _split_oversized_part(self, text: str, max_size: int) -> List[str]:
        """
        Split a single oversized part into pieces under max_size.
        Tries multiple splitting strategies from gentlest to most aggressive.
        """
        if len(text) <= max_size:
            return [text]
        
        # Strategy A: Split by markdown headers
        # (check with the same regex we split on; a looser check such as
        # "\n##" in text would match "\n##x", split nothing, and recurse forever)
        if re.search(r'\n(?=#{1,3}\s)', text):
            return self._split_by_pattern(text, r'\n(?=#{1,3}\s)', max_size)
        
        # Strategy B: Split by double newlines (paragraphs)
        if "\n\n" in text:
            return self._split_by_pattern(text, r'\n\n', max_size, keep_separator="\n\n")
        
        # Strategy C: Split by single newlines (lines)
        if "\n" in text:
            return self._split_by_pattern(text, r'\n', max_size, keep_separator="\n")
        
        # Strategy D: Split by sentences
        if ". " in text:
            parts = text.split(". ")
            chunks = []
            current = ""
            for i, sent in enumerate(parts):
                piece = sent + (". " if i < len(parts) - 1 else "")
                if len(current) + len(piece) > max_size:
                    if current.strip():
                        chunks.append(current.strip())
                    current = piece
                else:
                    current += piece
            if current.strip():
                chunks.append(current.strip())
            return chunks
        
        # Strategy E: HARD CHARACTER SPLIT (last resort)
        chunks = []
        for i in range(0, len(text), max_size):
            chunks.append(text[i:i + max_size])
        return chunks


    def _split_by_pattern(
        self,
        text: str,
        pattern: str,
        max_size: int,
        keep_separator: str = "",
    ) -> List[str]:
        """
        Generic splitter that respects max_size.
        Splits by regex pattern, then merges pieces under max_size.
        """
        parts = re.split(pattern, text)
        chunks = []
        current = ""
        
        for i, part in enumerate(parts):
            # For regex with capture group, re-add separator
            piece = part
            if keep_separator and i > 0:
                piece = keep_separator + part
            
            if len(current) + len(piece) > max_size:
                if current.strip():
                    chunks.append(current.strip())
                current = piece
                if len(piece) > max_size:
                    # Single piece too big - recurse with a finer strategy.
                    # strip() removes the re-added separator; otherwise the
                    # piece would be split on that same separator forever.
                    sub = self._split_oversized_part(piece.strip(), max_size)
                    chunks.extend(sub[:-1])
                    current = sub[-1] if sub else ""
            else:
                current += piece
        
        if current.strip():
            chunks.append(current.strip())
        
        return chunks
        
    def _create_chunk(
        self,
        content: str,
        metadata: Dict[str, Any],
        section: str,
        code_blocks: List[str]
    ) -> DocumentChunk:
        """Create a DocumentChunk from content."""
        content = content.strip()
        chunk_id = make_chunk_id(metadata.get("url", ""), content)

        return DocumentChunk(
            chunk_id=chunk_id,
            content=content,
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
            chunk_id = make_chunk_id(metadata.get("url", ""), chunk_content)
            
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