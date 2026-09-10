# scripts/analyze_urls.py
"""Analyze which URLs we've scraped to understand coverage."""
import json
from collections import Counter
from urllib.parse import urlparse


def analyze_urls(file_path: str = "data/raw/chunks.json"):
    with open(file_path, "r", encoding="utf-8") as f:
        chunks = json.load(f)
    
    urls = set(c["source_url"] for c in chunks)
    
    print(f"\n{'='*60}")
    print(f"URL COVERAGE ANALYSIS")
    print(f"{'='*60}")
    print(f"Unique URLs scraped: {len(urls)}\n")
    
    # Categorize URLs
    categories = Counter()
    for url in urls:
        path = urlparse(url).path
        parts = [p for p in path.split("/") if p]
        
        if "reference/services" in path:
            categories["reference/service"] += 1
        elif "reference" in path:
            categories["reference/other"] += 1
        elif "guide" in path:
            categories["guide"] += 1
        else:
            categories["other"] += 1
    
    print("URL Categories:")
    for cat, count in categories.most_common():
        print(f"  {cat}: {count}")
    
    print(f"\nAll URLs:")
    for url in sorted(urls):
        print(f"  {url}")


if __name__ == "__main__":
    analyze_urls()