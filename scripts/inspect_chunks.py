# scripts/inspect_chunks.py
"""
Inspect scraped chunks to verify quality.
"""
import sys
import os
import json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def inspect_chunks(file_path: str = "data/raw/chunks.json"):
    """Analyze chunks and print statistics."""
    
    with open(file_path, "r", encoding="utf-8") as f:
        chunks = json.load(f)
    
    print(f"\n{'='*70}")
    print(f"CHUNK ANALYSIS")
    print(f"{'='*70}")
    print(f"Total chunks: {len(chunks)}\n")
    
    # Overall statistics
    sizes = [len(c["content"]) for c in chunks]
    total_code_blocks = sum(len(c.get("code_blocks", [])) for c in chunks)
    
    print(f"📊 OVERALL STATISTICS")
    print(f"   Content sizes - Min: {min(sizes)}, Max: {max(sizes)}, Avg: {sum(sizes)//len(sizes)}")
    print(f"   Total code blocks: {total_code_blocks}")
    print(f"   Chunks with code: {sum(1 for c in chunks if c.get('code_blocks'))}")
    
    # Group by URL
    by_url = {}
    for chunk in chunks:
        url = chunk["source_url"]
        by_url.setdefault(url, []).append(chunk)
    
    print(f"\n{'='*70}")
    print(f"PER-PAGE BREAKDOWN")
    print(f"{'='*70}")
    
    for url, page_chunks in by_url.items():
        short_url = url.replace("https://boto3.amazonaws.com/v1/documentation/api/latest/", "")
        sizes = [len(c["content"]) for c in page_chunks]
        page_code = sum(len(c.get("code_blocks", [])) for c in page_chunks)
        
        print(f"\n📄 {short_url}")
        print(f"   Chunks: {len(page_chunks)}")
        print(f"   Sizes: {min(sizes)}-{max(sizes)} chars (avg: {sum(sizes)//len(sizes)})")
        print(f"   Code blocks: {page_code} {'✅' if page_code > 0 else '❌'}")
        
        # Show sections
        sections = [c["section"][:40] for c in page_chunks]
        print(f"   Sections: {sections}")
    
    # Show sample chunk with code
    print(f"\n{'='*70}")
    print("SAMPLE CHUNK WITH CODE")
    print(f"{'='*70}")
    
    chunks_with_code = [c for c in chunks if c.get("code_blocks")]
    if chunks_with_code:
        chunk = chunks_with_code[0]
        print(f"Section: {chunk['section']}")
        print(f"Length: {len(chunk['content'])} chars")
        print(f"Code blocks: {len(chunk['code_blocks'])}")
        print(f"\nContent:\n{chunk['content'][:500]}...")
        print(f"\nFirst code block:\n{chunk['code_blocks'][0][:300]}...")
    else:
        print("❌ NO CHUNKS WITH CODE FOUND")


if __name__ == "__main__":
    inspect_chunks()