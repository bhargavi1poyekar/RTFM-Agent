# scripts/test_scraper.py
"""
Test script for Boto3 scraper.
Run this to verify scraping works correctly.
"""
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.rtfm_agent.ingestion.boto3_scraper import Boto3Scraper
from config.logging import logger


def test_scraper():
    """Test the Boto3 scraper on a single page."""
    
    logger.info("Testing Boto3 scraper...")
    
    scraper = Boto3Scraper(
        base_url="https://boto3.amazonaws.com/v1/documentation/api/latest/index.html",
        output_dir="data/raw"
    )
    
    test_url = "https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3.html"
    
    logger.info(f"Fetching: {test_url}")
    page_data = scraper.get_page_content(test_url)
    
    if page_data:
        content = page_data.get('content', '')
        code_blocks = page_data.get('code_blocks', [])
        
        logger.info(f"✅ Title: {page_data.get('title')}")
        logger.info(f"✅ Content length: {len(content):,} chars")
        logger.info(f"✅ Code blocks: {len(code_blocks)}")
        
        # Test chunking
        chunks = scraper.chunk_content(
            content,
            {"url": test_url, "title": page_data.get('title', '')}
        )
        
        logger.info(f"✅ Chunks created: {len(chunks)}")
        
        # Chunk quality analysis
        sizes = [len(c.content) for c in chunks]
        logger.info(f"✅ Chunk size - min: {min(sizes)}, max: {max(sizes)}, avg: {sum(sizes)//len(sizes)}")
        
        # Save sample chunks for inspection
        import json
        sample_file = "data/raw/sample_chunks.json"
        with open(sample_file, "w", encoding="utf-8") as f:
            json.dump(
                [c.to_dict() for c in chunks[:5]],
                f,
                indent=2,
                ensure_ascii=False
            )
        logger.info(f"✅ Sample chunks saved to {sample_file}")
        
    else:
        logger.error("❌ Failed to fetch page")
    
    logger.info("Test complete!")


def test_full_scrape():
    """Test full scrape."""
    logger.info("Running full scrape test (5 pages max)...")
    
    scraper = Boto3Scraper()
    chunks = scraper.scrape(max_pages=5)
    
    # Quality report
    logger.info(f"✅ Scraped {len(scraper.visited_urls)} pages")
    logger.info(f"✅ Created {len(chunks)} chunks")
    
    if chunks:
        sizes = [len(c.content) for c in chunks]
        total_code = sum(len(c.code_blocks) for c in chunks)
        
        logger.info(f"✅ Chunk sizes - min: {min(sizes)}, max: {max(sizes)}, avg: {sum(sizes)//len(sizes)}")
        logger.info(f"✅ Total code blocks extracted: {total_code}")
        
        # Show section distribution
        sections = {}
        for chunk in chunks:
            sections[chunk.section] = sections.get(chunk.section, 0) + 1
        
        logger.info(f"✅ Unique sections: {len(sections)}")
    
    scraper._save_chunks()
    logger.info(f"✅ Saved chunks to {scraper.output_dir / 'chunks.json'}")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()
    
    if args.full:
        test_full_scrape()
    else:
        test_scraper()