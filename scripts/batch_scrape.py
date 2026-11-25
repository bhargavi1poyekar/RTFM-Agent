# scripts/batch_scrape.py
"""
Batch scrape Boto3 documentation with progress tracking and validation.
"""
import sys
import os
import time
import json
from pathlib import Path
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.rtfm_agent.ingestion.boto3_scraper import Boto3Scraper
from src.rtfm_agent.ingestion.validator import ChunkValidator
from config.logging import logger


def batch_scrape(
    max_pages: int = 500,
    output_dir: str = "data/raw",
    validate: bool = True,
    use_multi_seed: bool = True,
):
    """
    Scrape documentation with progress tracking and quality validation.
    
    Args:
        max_pages: Maximum pages to scrape
        output_dir: Directory to store output
        validate: Whether to run validation on results
    """
    logger.info(f"Starting batch scrape: max_pages={max_pages}")
    start_time = time.time()
    
    # Initialize scraper with rate limiting
    scraper = Boto3Scraper(
        output_dir=output_dir,
        requests_per_second=2.0,  # 2 requests/sec = polite
    )

    if use_multi_seed:
        seeds = scraper.get_all_seeds()
        logger.info(f"Using {len(seeds)} seed URLs for comprehensive coverage")
    else:
        seeds = None  # Falls back to base_url

    
    # Scrape
    chunks = scraper.scrape(max_pages=max_pages, seed_urls=seeds)

    
    
    # Compute duration
    duration = time.time() - start_time
    minutes = int(duration // 60)
    seconds = int(duration % 60)
    
    # Validation
    if validate and chunks:
        logger.info("Running validation...")
        # Uses the same size limits as the chunker (config/settings.py)
        validator = ChunkValidator()
        report = validator.validate(chunks)
        print("\n" + str(report))
    
    logger.info(f"""
    ============ SCRAPE COMPLETE ============
    Pages scraped: {len(scraper.visited_urls)}
    Chunks created: {len(chunks)}
    Time taken: {minutes}m {seconds}s
    Rate: {len(scraper.visited_urls) / max(duration, 1e-6):.2f} pages/sec
    Output: {scraper.output_dir}/chunks.json
    ========================================
    """)


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-pages", type=int, default=500)
    parser.add_argument("--output-dir", type=str, default="data/raw")
    parser.add_argument("--no-validate", action="store_true")
    args = parser.parse_args()
    
    batch_scrape(
        max_pages=args.max_pages,
        output_dir=args.output_dir,
        validate=not args.no_validate,
    )