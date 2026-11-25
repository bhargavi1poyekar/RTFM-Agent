# src/rtfm_agent/ingestion/validator.py
"""
Data quality validation for scraped chunks.
Ensures chunks meet quality standards before embedding.
"""
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
from collections import Counter

from src.rtfm_agent.ingestion.base import DocumentChunk
from config.logging import logger
from config.settings import settings


@dataclass
class ValidationReport:
    """Report of chunk quality validation."""
    total_chunks: int
    valid_chunks: int
    invalid_chunks: int
    issues: Dict[str, int]
    stats: Dict[str, Any]
    
    @property
    def pass_rate(self) -> float:
        """Percentage of chunks that pass validation."""
        if self.total_chunks == 0:
            return 0.0
        return (self.valid_chunks / self.total_chunks) * 100
    
    def __str__(self) -> str:
        """Pretty print the report."""
        lines = [
            "=" * 60,
            "VALIDATION REPORT",
            "=" * 60,
            f"Total chunks: {self.total_chunks}",
            f"Valid chunks: {self.valid_chunks} ({self.pass_rate:.1f}%)",
            f"Invalid chunks: {self.invalid_chunks}",
            "",
            "Issues found:",
        ]
        for issue, count in sorted(self.issues.items(), key=lambda x: -x[1]):
            lines.append(f"  - {issue}: {count}")
        
        lines.append("")
        lines.append("Statistics:")
        for key, value in self.stats.items():
            lines.append(f"  - {key}: {value}")
        
        lines.append("=" * 60)
        return "\n".join(lines)


class ChunkValidator:
    """
    Validates chunks for quality issues.
    
    Checks:
    - Content length (too short, too long)
    - Empty content
    - Duplicate chunks
    - Missing metadata
    - Code block quality
    """
    
    def __init__(
        self,
        min_length: Optional[int] = None,
        max_length: Optional[int] = None,
        min_code_length: int = 20,
    ):
        # Default to the same limits the chunker uses (config/settings.py)
        self.min_length = min_length or settings.CHUNK_MIN_SIZE
        self.max_length = max_length or settings.CHUNK_SIZE
        self.min_code_length = min_code_length

    @staticmethod
    def _fingerprints(content: str) -> Tuple[str]:
        """
        Fingerprint for duplicate detection: the whole (whitespace-normalised)
        content. Prefix fingerprints are NOT safe here: Boto3 pages share
        boilerplate openings (e.g. every service's "Waiters are available...")
        followed by different content, so prefix matching discarded real data.
        Used by both validate() and filter_valid().
        """
        return (" ".join(content.split()),)
    
    def validate(self, chunks: List[DocumentChunk]) -> ValidationReport:
        """Validate all chunks and generate a report."""
        logger.info(f"Validating {len(chunks)} chunks")
        
        issues = Counter()
        valid_chunks = []
        
        # Track content fingerprints for duplicate detection
        seen_fingerprints = set()
        
        for chunk in chunks:
            chunk_issues = self._validate_chunk(chunk, seen_fingerprints)
            
            if chunk_issues:
                for issue in chunk_issues:
                    issues[issue] += 1
            else:
                valid_chunks.append(chunk)
                # Record fingerprints for dedup
                seen_fingerprints.update(self._fingerprints(chunk.content))
        
        # Compute statistics
        stats = self._compute_stats(valid_chunks)
        
        report = ValidationReport(
            total_chunks=len(chunks),
            valid_chunks=len(valid_chunks),
            invalid_chunks=len(chunks) - len(valid_chunks),
            issues=dict(issues),
            stats=stats,
        )
        
        logger.info(f"Validation complete. Pass rate: {report.pass_rate:.1f}%")
        return report
    
    def _validate_chunk(
        self,
        chunk: DocumentChunk,
        seen_fingerprints: set,
    ) -> List[str]:
        """Validate a single chunk. Returns list of issues."""
        issues = []
        
        # 1. Content length
        content_len = len(chunk.content)
        
        if content_len == 0:
            issues.append("empty_content")
        elif content_len < self.min_length:
            issues.append("too_short")
        elif content_len > self.max_length:
            issues.append("too_long")
        
        # 2. Required metadata
        if not chunk.source_url:
            issues.append("missing_source_url")
        if not chunk.title:
            issues.append("missing_title")
        if not chunk.section:
            issues.append("missing_section")
        
        # 3. Content quality
        content_stripped = chunk.content.strip()
        
        # All whitespace?
        if not content_stripped:
            issues.append("whitespace_only")
        
        # Just a header with no content?
        if content_stripped.startswith("#") and len(content_stripped.split("\n")) < 2:
            issues.append("header_only")

        # Too many URLs relative to content (navigation page)
        url_count = content_stripped.count("http")
        if url_count > 10 and url_count * 50 > content_len:
            issues.append("navigation_heavy")

        # 4. Duplicate detection (exact or near-duplicate prefix)
        if any(fp in seen_fingerprints for fp in self._fingerprints(chunk.content)):
            issues.append("duplicate")

        # 5. Code block quality
        for code in chunk.code_blocks:
            if len(code) < self.min_code_length:
                issues.append("tiny_code_block")
                break
        
        return issues
    
    def _compute_stats(self, chunks: List[DocumentChunk]) -> Dict[str, Any]:
        """Compute statistics on valid chunks."""
        if not chunks:
            return {}
        
        sizes = [len(c.content) for c in chunks]
        code_counts = [len(c.code_blocks) for c in chunks]
        
        return {
            "size_min": min(sizes),
            "size_max": max(sizes),
            "size_avg": sum(sizes) // len(sizes),
            "size_median": sorted(sizes)[len(sizes) // 2],
            "chunks_with_code": sum(1 for c in code_counts if c > 0),
            "total_code_blocks": sum(code_counts),
            "unique_sources": len(set(c.source_url for c in chunks)),
            "unique_sections": len(set(c.section for c in chunks)),
        }
    
    def filter_valid(self, chunks: List[DocumentChunk]) -> List[DocumentChunk]:
        """Return only valid chunks."""
        seen_fingerprints = set()
        valid = []
        
        for chunk in chunks:
            issues = self._validate_chunk(chunk, seen_fingerprints)
            if not issues:
                valid.append(chunk)
                seen_fingerprints.update(self._fingerprints(chunk.content))
        
        return valid