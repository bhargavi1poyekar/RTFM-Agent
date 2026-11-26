# RTFM Agent: Learning Manual

A walkthrough of what has been built, how each piece works, why it was designed that way, and how to talk about it in an interview.

> Status snapshot as of 25 Nov 2026. Update section 2 as you build more.

---

## Table of contents

1. [What is this project?](#1-what-is-this-project)
2. [Where are we? (progress tracker)](#2-where-are-we-progress-tracker)
3. [The big picture: how RAG works](#3-the-big-picture-how-rag-works)
4. [Project layout](#4-project-layout)
5. [Part A: Project foundation (config, logging, API, tooling)](#5-part-a-project-foundation)
6. [Part B: The ingestion pipeline (the real work so far)](#6-part-b-the-ingestion-pipeline)
7. [Part C: Data quality validation](#7-part-c-data-quality-validation)
8. [How to run everything](#8-how-to-run-everything)
9. [Health check results (what works, what doesn't)](#9-health-check-results)
10. [Known issues and improvements](#10-known-issues-and-improvements)
11. [What's next: the roadmap and concepts to learn](#11-whats-next)
12. [Interview prep: questions and model answers](#12-interview-prep)
13. [Glossary](#13-glossary)
14. [Hands-on exercises](#14-hands-on-exercises)

---

## 1. What is this project?

**One-liner (memorise this):**
> RTFM Agent is a RAG-based assistant for AWS. You ask a question like "how do I upload a file to S3 with encryption?" and it answers with working Boto3 Python code, the minimal IAM policy you need, and citations to the official documentation it used.

**Why it's useful:** LLMs on their own often invent Boto3 method names or parameters (they *hallucinate*). By retrieving the real documentation first and making the LLM answer from it, the answers are grounded and up to date, and the user can check the cited source.

**Name:** "RTFM" = "Read The F***ing Manual". The agent reads the manual so you don't have to.

---

## 2. Where are we? (progress tracker)

| # | Stage | Status | Where in code |
|---|-------|--------|---------------|
| 0 | Project setup: config, logging, FastAPI skeleton, Docker, Makefile, pre-commit | ✅ Done | `config/`, `src/rtfm_agent/main.py`, `run.py`, `dockerfile`, `makefile` |
| 1 | **Ingestion**: crawl Boto3 docs, HTML→Markdown, preserve code, chunk | ✅ Done (crawler bugs #1–4 fixed 25 Nov; design items in §10 remain) | `src/rtfm_agent/ingestion/base.py`, `boto3_scraper.py` |
| 2 | **Validation**: data-quality checks on chunks | ✅ Done | `src/rtfm_agent/ingestion/validator.py` |
| 3 | Dataset: 2,300 pages of the 10 focus services → 26,317 chunks, **16,697 unique** (rest are true duplicates, e.g. EC2's resource-type list repeated on 107 pages). Use `ChunkValidator().filter_valid()` before embedding: the file stores all chunks, including duplicates. | ✅ Re-scraped 25 Nov (uncommitted, 34 MB) | `data/raw/chunks.json` |
| 4 | Automated tests (pytest) | ✅ 42 offline tests: scraper, chunking, validator, API | `tests/` |
| 4b | Chunk size fitted to embedding model (700 chars); PyTorch fixed | ✅ Done 25 Nov. `chunks.json` must be re-scraped to use it | `config/settings.py` |
| 5 | **Embedding**: turn chunks into vectors | ❌ Not started | |
| 6 | **Vector store**: load vectors into ChromaDB | ❌ Not started | |
| 7 | **Retrieval**: find top-k chunks for a question | ❌ Not started | |
| 8 | **Generation**: prompt the LLM with retrieved chunks | ❌ Not started | |
| 9 | API endpoint `/api/v1/query` | ❌ Not started (router commented out) | |
| 10 | Conversation memory (multi-turn) | ❌ Not started | |
| 11 | IAM policy generation | ❌ Not started | |
| 12 | Evaluation (is retrieval/answer quality good?) | ❌ Not started | |
| 13 | Frontend (Streamlit), CI (GitHub Actions), cloud deploy | ❌ Not started | |

**Honest summary:** roughly **25–30% done**. The foundation and the data pipeline (the "R" prep part of RAG) are built and working. Everything that makes it *answer questions* (embed → store → retrieve → generate) is still to come. That's normal: data ingestion is often the hardest and messiest part, and it's done.

---

## 3. The big picture: how RAG works

**RAG = Retrieval-Augmented Generation.** Two phases:

```
 OFFLINE (run once / on a schedule)                        ← we are here
 ┌────────────┐   ┌──────────────┐   ┌──────────┐   ┌──────────┐   ┌─────────────┐
 │ Boto3 docs │──▶│ Scrape+clean │──▶│  Chunk   │──▶│  Embed   │──▶│ Vector DB   │
 │  (HTML)    │   │ HTML→Markdown│   │ ~1.5k ch │   │ (MiniLM) │   │ (ChromaDB)  │
 └────────────┘   └──────────────┘   └──────────┘   └──────────┘   └─────────────┘
                     ✅ done           ✅ done        ❌ next        ❌ next

 ONLINE (every user question)
 ┌──────────┐   ┌──────────┐   ┌──────────────┐   ┌────────────┐   ┌──────────────┐
 │ Question │──▶│  Embed   │──▶│ Search top-k │──▶│ Prompt LLM │──▶│ Answer + code│
 │          │   │ question │   │ similar chunks│  │ with chunks│   │ + citations  │
 └──────────┘   └──────────┘   └──────────────┘   └────────────┘   └──────────────┘
```

Key idea to explain in interviews: **the LLM doesn't "know" the docs. We find the relevant docs and paste them into the prompt.** The quality of the answer is capped by the quality of what we retrieve, and what we retrieve is capped by how well we cleaned and chunked the data. That's why ingestion matters so much.

---

## 4. Project layout

```
RTFM/
├── config/
│   ├── settings.py        # All configuration (Pydantic Settings, reads .env)
│   └── logging.py         # Structured JSON logging
├── src/rtfm_agent/
│   ├── __init__.py        # Package init; sets up logging on import
│   ├── main.py            # FastAPI app (/health, /)
│   └── ingestion/
│       ├── base.py        # DocumentChunk dataclass + BaseScraper (abstract)
│       ├── boto3_scraper.py  # Concrete scraper for Boto3 docs
│       └── validator.py   # Chunk quality checks + report
├── scripts/
│   ├── test_scraper.py    # Manual smoke test: scrape 1 page (or 5 with --full)
│   ├── batch_scrape.py    # Real run: crawl up to N pages + validate
│   └── inspect_chunks.py  # Print stats about chunks.json
├── data/
│   ├── raw/chunks.json    # OUTPUT of ingestion (2,034 chunks)
│   ├── processed/         # (empty, for later)
│   └── embeddings/chromadb/  # (empty, vector DB will live here)
├── requirements/          # base / dev / prod dependency split
├── tests/  docs/  .github/workflows/   # empty placeholders
├── run.py                 # Starts the dev server with uvicorn
├── dockerfile, makefile, pyproject.toml, .pre-commit-config.yaml
└── .env                   # Local secrets/config (git-ignored ✅)
```

---

## 5. Part A: Project foundation

### 5.1 Configuration: `config/settings.py`

Uses **Pydantic Settings** (`BaseSettings`). Every setting has a typed default, and any of them can be overridden by an environment variable or the `.env` file.

```python
class Settings(BaseSettings):
    LLM_PROVIDER: Literal["ollama", "openai", "anthropic"] = "ollama"
    RETRIEVAL_TOP_K: int = 5
    ...
settings = Settings()   # one shared instance (singleton)
```

**Why this design?**
- **12-Factor App principle:** config lives in the environment, not in code. Same code runs in dev, test and prod with different `.env`.
- **Type safety:** `Literal[...]` means a typo like `LLM_PROVIDER=olama` fails at startup instead of halfway through a request.
- **Secrets** (API keys) come from env vars, never hard-coded. `.env` is in `.gitignore`.

**Side effect to know about:** importing `settings` also creates the `data/`, `logs/`, `chromadb/` folders.

### 5.2 Logging: `config/logging.py`

Uses `python-json-logger` to print each log line as JSON:
```json
{"timestamp": "...", "level": "INFO", "logger": "rtfm_agent", "message": "...", "app": "RTFM Agent", "env": "development"}
```
**Why JSON logs?** In production, log tools (CloudWatch, GCP Logging, Datadog) can search and filter by field (`level=ERROR AND env=production`). Plain text is hard to query. Set `LOG_FORMAT=text` for human-readable local logs.

It also quiets noisy libraries (`urllib3`, `requests`, `chromadb`) to WARNING.

### 5.3 The API: `src/rtfm_agent/main.py`

A **FastAPI** app with:
- `lifespan` context manager: code before `yield` runs at startup, after runs at shutdown. This is where we'll later load the embedding model and connect to ChromaDB **once**, instead of on every request (loading a model per request would be very slow).
- **CORS middleware**: lets a browser frontend on another domain call the API.
- `GET /health`: health check. Load balancers / Cloud Run / Kubernetes call this to know if the container is alive.
- `GET /`: welcome message. FastAPI auto-generates interactive docs at `/docs` (Swagger UI).

`run.py` starts it with **uvicorn** (an ASGI server), with auto-reload in development.

### 5.4 Tooling

| Tool | What it does | Why |
|---|---|---|
| `requirements/base|dev|prod.txt` | Layered dependencies; dev and prod both include base | Prod image stays small (no pytest/black) |
| `makefile` | Shortcuts: `make test`, `make lint`, `make run` | One standard way to do common tasks |
| `.pre-commit-config.yaml` | Runs black, isort, flake8, mypy before every commit | Catches style/type errors before they reach git |
| `pyproject.toml` | Config for black (line length 88), mypy (strict), pytest | One place for tool settings |
| `dockerfile` | Python 3.11-slim image, non-root user, pre-downloads embedding model | Reproducible deploys; non-root = security best practice; pre-download = fast cold start |

---

## 6. Part B: The ingestion pipeline

This is the core work done so far. Goal: **turn the Boto3 documentation website into clean, self-contained text chunks** ready to be embedded.

### 6.1 The data unit: `DocumentChunk` (base.py)

```python
@dataclass
class DocumentChunk:
    chunk_id: str        # md5 hash → stable unique ID
    content: str         # the markdown text (what gets embedded)
    source_url: str      # for citations
    title: str           # page title, e.g. "send_message - Boto3 docs"
    section: str         # heading the chunk came from
    metadata: dict       # url, title, scraped_at, page size
    code_blocks: list    # code snippets found in this chunk
    created_at: str
```

**Why keep `source_url`?** Citations. The answer can say "source: …/sqs/client/send_message.html", which builds trust and lets users verify.

**Why a deterministic hash for `chunk_id`?** `md5(url + section + first 100 chars)` gives the same ID every time you scrape the same content. When we load into ChromaDB, re-running ingestion can **upsert** (update-or-insert) instead of creating duplicates. This makes the pipeline **idempotent** (safe to run twice). md5 here is for identity, not security, so it's fine.

### 6.2 `BaseScraper`: Template Method pattern (base.py)

`BaseScraper` is an **abstract base class (ABC)**. It implements the *crawl loop* once (`scrape()`), and declares abstract methods each documentation site must implement:

| Abstract method | Job |
|---|---|
| `get_page_content(url)` | Download page, return title/content/code |
| `get_links(url)` | Find more doc URLs to visit |
| `clean_content(html)` | HTML → clean text |
| `extract_code_blocks(html)` | Pull out code |
| `chunk_content(text, meta)` | Split into `DocumentChunk`s |

This is the **Template Method design pattern**: the skeleton of the algorithm is fixed in the parent; the steps are filled in by the child. To add another doc source later (e.g. AWS CLI docs, Terraform docs), you write a new subclass and reuse the crawl loop, rate limiting, and saving.

#### The crawl loop (`scrape()`), step by step

```
queue = prioritize(seed_urls)
while queue not empty and pages < max_pages:
    url = queue.pop(0)                    # take highest-priority URL
    skip if already visited
    page = get_page_content(url)          # fetch + clean
    for link in get_links(url):           # discover new URLs
        add to queue if new
    every 10 pages: re-sort the queue by priority
    chunks += chunk_content(page)
    mark visited
save all chunks to data/raw/chunks.json
```

Things to notice and be able to explain:
- It's a **crawler**: a graph traversal (breadth-first-ish) over web pages, where links are edges.
- `visited_urls` (a set) prevents infinite loops (page A links to B links to A).
- `max_pages` is a safety limit.
- Each page is wrapped in `try/except`: one broken page doesn't kill a 500-page run. It's logged and skipped.
- **Rate limiting** (`_rate_limit`): at most 2 requests/second. This is *polite crawling*: we don't hammer AWS's servers, and we avoid getting blocked.

### 6.3 `Boto3Scraper`: the concrete implementation

#### a) Seeds and prioritisation
- `REFERENCE_SEEDS`: start URLs for the 10 most-used AWS services (S3, EC2, IAM, Lambda, DynamoDB, SQS, SNS, CloudWatch, CloudFormation, RDS).
- The **focus services** are taken from the seed list (`focus_services` property).
- `prioritize_urls()` sorts the queue by **tier**, then **round-robin across services** within a tier:

  | Tier | Pages | Example |
  |---|---|---|
  | 5 | focus service landing page | `services/s3.html` |
  | 4 | focus service client/resource pages (the API methods) | `s3/client/put_object.html` |
  | 3 | focus service paginators/waiters (mostly boilerplate) | `s3/paginator/ListObjectsV2.html` |
  | 2 | guides | `guide/quickstart.html` |
  | 1 | reference index pages | `reference/index.html` |
  | 0 | everything else, incl. ~400 other services | `services/waf.html` |

  Round-robin means the order is S3, EC2, IAM, … then S3, EC2, IAM, …, so EC2's 1,077 pages can't use up a small budget before Lambda gets a turn.

  **Why?** Boto3 has 400+ services and tens of thousands of pages. A code assistant needs the *method* pages (parameters, request syntax, examples) of the services people actually use.

  **War story (great interview material):** the first full run looked healthy: 500 pages, 0 errors, 94% validation pass. But checking *coverage* showed 445 of the 500 pages were landing pages of random services, and S3, EC2 and IAM had **1 page each**, with no `put_object` page at all. Two bugs together caused it: (1) the old rule "shorter path = higher priority" ranked every service's landing page above any method page; (2) `get_links` kept only the first 100 links, and on a service page the first ~520 links are the sidebar, so the method links (positions 520+) were always cut off. Lesson: **"no errors" ≠ "correct data". Check what you collected against what you meant to collect.**
- `exclude_patterns`: skip `/_static`, `/search`, `/genindex`, `/changelog`, etc., which are not useful content.
- Only follows links on `boto3.amazonaws.com`, strips `#fragments` so `page.html#a` and `page.html#b` count as one page. No per-page link cap (see war story). The crawl loop tracks queued URLs in a `set`, because pages can have 1,600 links and checking membership in a list would be slow.

#### b) Fetching & extracting the main content (`get_page_content`)
1. `requests.Session` (reuses TCP connections = faster), with a custom User-Agent identifying the bot.
2. Parse with **BeautifulSoup + lxml**.
3. Find the main content: `div.body` (Sphinx docs) → fallback `<main>` → `<article>`.
4. Delete `nav`, `footer`, `aside` (menus and sidebars are noise that would pollute embeddings).

#### c) HTML → Markdown while keeping code intact (`clean_content`): **the clever bit**

Problem: `html2text` (HTML→Markdown converter) tends to mangle code blocks (indentation, wrapping, escaping). Code is the most valuable content for this project.

Solution: the **placeholder trick**:
```
1. Find every code container (Sphinx <div class="highlight-python">, or bare <pre>)
   - take only the OUTERMOST container so nested ones aren't captured twice
2. Save each one as a proper markdown block  ```python ... ```
   and replace it in the HTML with a token  __CODEBLOCK_3__
3. Run html2text on the rest (tokens are plain text, so they pass through untouched)
4. Swap each token back for its saved code block
5. Collapse 3+ blank lines into 2
```
Interview phrasing: *"I protected code blocks from the converter by swapping them out for placeholders, converting the prose, and swapping them back. It's the same idea as escaping."*

`_detect_language_from_container` labels each block (`python`, `json`, `bash`…) using Sphinx CSS classes first (`highlight-default` = Python in Sphinx), then content heuristics (`import boto3` → python, starts with `{` → json).

#### d) Chunking (`chunk_content`): the most important design decision

**Why chunk at all?**
1. Embedding models have an input limit; text past it is ignored.
2. One vector for a whole 30-page document is a blurry average; small chunks give precise matches.
3. The LLM's context window is limited and costs money per token; we want to send only the relevant ~5 pieces.

**Trade-off:** too small = chunks lose context ("it returns a dict"… what does?). Too big = retrieval is imprecise, wastes tokens, and **anything past the embedding model's input limit is silently ignored**. Here: **min 40, max 230 tokens**, counted with the embedding model's own tokenizer (`src/rtfm_agent/ingestion/tokens.py`), set once in `config/settings.py` (`CHUNK_MIN_TOKENS`, `CHUNK_MAX_TOKENS`) and used by both the chunker and the validator.

**Why 230 tokens:** the model reads 256 tokens, 2 of which are `[CLS]` and `[SEP]` (so 254 are left), and each chunk is embedded with a breadcrumb like `s3 > client > put_object` whose longest real example is 23 tokens. 254 − 24 = 230, so chunk + breadcrumb always fits. A reader of the Part 3 blog post pointed out that characters are only a proxy: chars/token has a median of 3.7 but drops to ~2.3 for the bottom 1% and 1.34 for EC2 instance-type lists, where 700 chars was 519 tokens. Measuring the real unit fixes that for every chunk. Gotcha: the tokenizer maps any single "word" over 100 chars to one `[UNK]` token.

**History: how 700 characters was chosen first (measured, but still a proxy):** `all-MiniLM-L6-v2` truncates at 256 tokens. Running its tokenizer over the old chunks showed Boto3 docs average **3.2 chars/token** (2.8 for code-dense text, since identifiers like `put_bucket_lifecycle_configuration` split into many tokens). 256 × 2.8 ≈ 700. Before/after on real pages:

| | Old (400–2,000 chars) | New (150–700 chars) |
|---|---|---|
| Median chunk | 493 tokens | 220 tokens |
| Chunks over 256 tokens | 80% | 7% (by ≤ 28 tokens) |
| Text the model never sees | **47%** | **0.3%** |

**Strategy: structure-aware chunking (not naive fixed-size):**
1. `_extract_section_hierarchy`: split the markdown at `##` headings. `###` sub-headings are folded into their parent section (bolded). "Note/Warning/Tip" boxes stay in their section rather than becoming tiny standalone chunks.
2. Walk the sections with a **buffer**:
   - **Section (including its `## heading`) > 700 chars** → flush buffer, then split the section with `_split_large_section`. (The heading used to be added *after* the size check, which produced over-limit chunks.)
   - **Fits in buffer** → append (merge small neighbouring sections together).
   - **Doesn't fit** → flush the buffer as a chunk, start a new one.
   - Chunks under 150 chars are dropped (usually fragments/noise).
3. `_split_large_section` uses a **cascading/recursive split**, gentlest first:
   1. around code blocks (never cut a code block in half if avoidable)
   2. at markdown headings
   3. at paragraphs (`\n\n`)
   4. at lines (`\n`)
   5. at sentences (`. `)
   6. hard character cut (last resort)

   This is the same idea as LangChain's `RecursiveCharacterTextSplitter`, written by hand. Be ready to say: *"I split at the most semantically meaningful boundary that still respects the size limit."*
   **Recursion safety:** each recursive call must move to a *finer* strategy, or it loops forever. Two old bugs broke that (a paragraph right after a code block, and text containing `\n##x`); both raised `RecursionError` and the page was silently skipped. Fixed by stripping the re-added separator before recursing, and by checking for headings with the same regex used to split on them.
4. `_deduplicate_chunks`: drop chunks whose **whole** text (whitespace-normalised) matches an earlier chunk on the same page. It used to compare only the first 100–300 characters, which wrongly dropped different sections that share boilerplate openings.

There's also an unused `_fixed_size_chunking` fallback (1,000 chars with 100 overlap). **Overlap** means consecutive chunks share some text so a sentence cut at a boundary still appears whole in one of them.

### 6.4 Output

`data/raw/chunks.json`: a list of chunk dicts. Current run: **2,034 chunks from 490 pages** (480 API-reference pages, 9 guides, 1 other). The *committed* version in git is an older run (188 chunks / 50 pages). The new one is not committed yet.

---

## 7. Part C: Data quality validation

`validator.py` → `ChunkValidator.validate(chunks)` returns a `ValidationReport`.

**Checks per chunk:**
| Check | Catches |
|---|---|
| empty / too_short / too_long | broken extraction, fragments, oversized chunks |
| missing_source_url / title / section | metadata bugs (citations would break) |
| whitespace_only, header_only | chunks with no real content |
| duplicate (same whole text, ignoring whitespace) | content repeated across pages |
| navigation_heavy (>10 URLs, URLs dominate text) | link-list/menu pages |
| tiny_code_block (<20 chars) | junk code snippets |

**Stats reported:** min/max/avg/median size, chunks with code, unique sources/sections, plus **pass rate**.

**Why validate before embedding?** "Garbage in, garbage out." Embedding costs compute, and junk chunks get retrieved and confuse the LLM. It's a **data quality gate**, the same idea as data validation in any ML/data pipeline (e.g. Great Expectations).

**Why whole-text and not a prefix?** Measured on 20 live pages: a 150-char prefix check flagged 33 "duplicates", but 22 of them were *different* content. Every service's Waiters section starts with the same sentence ("Waiters are available on a client instance…") and then lists different waiters, so EC2's list was thrown away because S3's came first. Lesson: in templated docs, shared prefixes are normal. With whole-text matching: 14 true duplicates, 97% pass rate.

**Old results** on the current `chunks.json` (scraped with the *old* 400–2,000 chunker, so it needs re-scraping before embedding):
```
Total 2,034 | Valid 2,006 (98.6%) | Invalid 28 (all "duplicate")
sizes: min 400, median 1,806, avg 1,484, max 2,002
437 chunks contain code (469 code blocks) | 490 unique source pages
```

---

## 8. How to run everything

Always activate the venv first (Windows PowerShell: `venv\Scripts\activate`; Git Bash: `source venv/Scripts/activate`). Run from the project root.

| Task | Command |
|---|---|
| Start API | `python run.py` → open http://localhost:8000/docs |
| Smoke-test scraper on 1 page | `python scripts/test_scraper.py` |
| Scrape 5 pages | `python scripts/test_scraper.py --full` |
| Full crawl + validation | `python scripts/batch_scrape.py --max-pages 500` (takes several minutes; overwrites `chunks.json`) |
| Inspect chunks | `python scripts/inspect_chunks.py` |
| Readable logs | set `LOG_FORMAT=text` in `.env` |

---

## 9. Health check results

Checked on 25 Nov 2026:

| What | Result |
|---|---|
| Python venv (3.11.0) & core deps (FastAPI, bs4, html2text, pydantic-settings) | ✅ import fine |
| FastAPI app: `/health` and `/` | ✅ 200 OK, startup/shutdown logs fire |
| Live scrape of 1 Boto3 page (SQS `send_message`) | ✅ 8 chunks, code block detected, ~0.8s |
| Validator on existing data | ✅ 98.6% pass rate |
| ChromaDB import | ✅ 0.5.0 |
| **PyTorch / sentence-transformers import** | ✅ Fixed. Was `OSError: [WinError 126] ... fbgemm.dll`. Reading `fbgemm.dll`'s import table showed it needs `libomp140.x86_64.dll`, which the **torch 2.4.0 Windows wheel doesn't ship**. Upgraded to torch 2.4.1 (pinned in `requirements/base.txt`), and installed torchvision 0.19.1 to match it, replacing an incompatible 0.29 that wasn't in requirements. Debugging lesson: "module could not be found" names the DLL that failed to load, not the missing dependency, so list its imports. |
| Tests (`python -m pytest -v`) | ✅ 42 passed in ~1 s |

---

## 10. Known issues and improvements

These are real findings from reading the code. Knowing your own project's weaknesses is a **strength** in interviews ("what would you do differently?").

**Bugs (✅ all fixed 25 Nov 2026, covered by `tests/test_boto3_scraper.py`)**
1. ✅ **`batch_scrape.py` ignored the multi-seed list.** It built `seeds` but called `scrape()` without `seed_urls=seeds`, so the crawl started only from the main index. Now passes the seeds; a live 3-page run starts at S3 → EC2 → IAM.
2. ✅ **Every page was downloaded twice** (`get_page_content()` and `get_links()` each called `session.get`). Fixed with `_fetch_html()`, which caches the *raw bytes* of the last page. Why bytes and not the parsed `soup`? `get_page_content()` deletes `<nav>` from its soup; sharing it would hide navigation links from `get_links()`. Live check: 3 pages = 3 requests (was 6).
3. ✅ `self._rate_limit()` sat above the docstrings, so they weren't docstrings. Rate limiting now lives in `_fetch_html()`, and only applies to real network calls (a cache hit doesn't wait).
4. ✅ `prioritize_urls()` was called by `BaseScraper` but only defined in `Boto3Scraper`. The base class now has a default (keep order = plain BFS) that subclasses override. That's the "hook method" variant of the Template Method pattern.
- ✅ **Crawler never reached method pages** (100-link cap + path-length priority). See the war story in §6.3a. Fixed with no cap, tiered round-robin priority, and tests for both.
- ✅ Bonus, found by the new test: `batch_scrape` crashed with `ZeroDivisionError` when a run took ~0 s (e.g. network down) while printing pages/sec. Division is now guarded.

**Design / quality**
5. ✅ **Chunk size vs. the embedding model (fixed).** Was 47% of text ignored by the 256-token limit; 0.3% with 700-char chunks (see §6.3 table); now **0%** with the token-based limit (230 tokens + breadcrumb + `[CLS]`/`[SEP]` ≤ 256 for every chunk). Cleaned pages are saved to `data/raw/pages.json`, so `rechunk_pages()` can re-chunk without re-crawling. Also, smaller chunks make *context* more important, so #6 below matters more now. Large code blocks (> 700 chars) can now be split across chunks; keeping each code block whole is a possible next improvement.
6. ✅ (worked around at embed time) **Weak `section` labels.** `vector_store.context_header()` now builds a breadcrumb from the URL (`sqs > client > send_message`) and embeds it with each chunk. Original note: Only 24 unique sections across 2,034 chunks ("Client" ×1,236, "Overview" ×374). The page's breadcrumb section is computed in `_get_section()` but thrown away. Better: store it in metadata, and prefix each chunk with its page title + heading before embedding (*contextual chunk headers*), so a chunk saying "Parameters: QueueUrl…" also carries "SQS › send_message".
7. ✅ Thresholds were inconsistent (chunker 400–2,000, validator 400–2,500, `batch_scrape` 200–3,000, and the heading could push a chunk over max). Now one source of truth: `settings.CHUNK_MAX_TOKENS` / `CHUNK_MIN_TOKENS`. (`CHUNK_OVERLAP` is still unused.)
8. 🟡 Queue membership check now uses a `set` (O(1)). Still O(n): `pop(0)` and re-sorting the whole queue every 10 pages. Fine for a few thousand pages; at much larger scale use a heap (`heapq`) keyed by priority.
9. ✅ Dedup (validator *and* scraper) used text prefixes. That counted some chunks twice, made `filter_valid()` disagree with `validate()`, and **discarded different content** that shared boilerplate openings (22 of 33 flagged chunks on a live sample). Now both compare the whole whitespace-normalised text through one shared helper.
10. No retries on network errors. `tenacity` is already in requirements, so wrap `session.get` in `@retry` with exponential backoff.

**Engineering hygiene**
11. ✅ **Tests: 42 passing in ~1 s, all offline.** Run with `python -m pytest -v`.
    - `test_boto3_scraper.py`: crawler with a `FakeSession` (stand-in for `requests.Session` that records URLs): one download per page, links survive `<nav>` removal, code preserved, seeds passed.
    - `test_chunking.py`: size limits, merging, no text lost, min-size drop, code blocks recorded, deterministic IDs, plus **regression tests** for each bug fixed (heading overshoot, both infinite recursions, prefix dedup). Each was confirmed to fail on the old code.
    - `test_validator.py`: one parametrised test per rule, report numbers, `filter_valid` agrees with `validate`.
    - `test_api.py`: `/health`, `/`, `/docs` via FastAPI's `TestClient`.
12. ✅ **`.dockerignore` added.** `COPY . .` used to copy `.env` (secrets), `venv/`, `.git/` and data into the image. Gotcha worth knowing: unlike `.gitignore`, a `.dockerignore` pattern such as `__pycache__` only matches at the project root, so nested folders need `**/__pycache__`. `data/embeddings/` is deliberately *not* excluded yet, because whether to ship a prebuilt vector index inside the image is a later decision. Still open: the stage is named `builder` but there's no second stage, so it isn't really multi-stage.
13. CORS `allow_origins=["*"]` with `allow_credentials=True` is too permissive for production. Restrict to the frontend's domain.
14. `pyproject.toml` says `readme = "README.md"` but the file is `Readme.md` (breaks on Linux, which is case-sensitive).
15. ✅ `chunks.json` is no longer tracked (the current one is 34 MB). Generated data belongs outside git; rebuild it with `python scripts/batch_scrape.py --max-pages 2300` (~20 min). `.gitignore` also covers the future ChromaDB index folder. The old 4 MB version still exists in git *history*; removing it from history would mean rewriting commits, which isn't worth it at this size.
16. User-Agent still has a placeholder GitHub URL.

---

## 11. What's next

Suggested order, with the concept you'll learn at each step:

1. **Fix bugs 1, 2, 5 and add tests.** *Learn:* pytest, fixtures, mocking HTTP.
2. **Embeddings** (`src/rtfm_agent/embeddings/`): load the model once, `model.encode(texts, batch_size=32, normalize_embeddings=True)`. *Learn:* what an embedding is, cosine similarity, why normalise, batching.
3. **Vector store** (`src/rtfm_agent/vectorstore/`): ChromaDB `PersistentClient(path=CHROMADB_PERSIST_DIR)`, `collection.upsert(ids=chunk_ids, documents=..., embeddings=..., metadatas=...)`. *Learn:* ANN search, HNSW index, metadata filtering (e.g. `where={"service": "s3"}`).
4. **Retriever**: embed question → top-k (5) → filter by score threshold (0.7 in settings). Later: **hybrid search** (vector + BM25 keyword, because exact names like `put_object` matter), **re-ranking** with a cross-encoder. *Learn:* recall vs precision, top-k tuning.
5. **Generator**: prompt template = system rules ("answer only from context, cite sources, say 'I don't know' if not in context") + retrieved chunks + question. Provider switch via `LLM_PROVIDER`. *Learn:* prompt engineering, grounding, hallucination control, token budgeting (`tiktoken`).
6. **API**: `POST /api/v1/query` with Pydantic request/response models; load model + DB in `lifespan`. *Learn:* dependency injection in FastAPI, async.
7. **Memory**: keep last `MAX_CONVERSATION_TURNS`; rewrite follow-ups ("what about with encryption?") into standalone questions before retrieval (*query rewriting*).
8. **IAM policy generation**: map the Boto3 calls in the answer (e.g. `s3.put_object`) to IAM actions (`s3:PutObject`) and emit a least-privilege policy JSON. *Learn:* principle of least privilege.
9. **Evaluation**: build ~50 question → expected-source pairs; measure **Recall@k / MRR** for retrieval and faithfulness for answers (e.g. RAGAS or LLM-as-judge). *Learn:* you can't improve what you don't measure.
10. **Ship**: Streamlit UI, GitHub Actions CI (lint + tests), Docker → Cloud Run.

---

## 12. Interview prep

**Q: Walk me through your project.**
> I'm building a RAG assistant for AWS's Boto3 SDK. It answers questions with working Python code and the IAM permissions needed, grounded in the official docs with citations. So far I've built the ingestion pipeline: a polite, rate-limited crawler that prioritises API-reference pages for the 10 most-used services, converts Sphinx HTML to Markdown while protecting code blocks with a placeholder technique, and chunks pages using structure-aware, recursive splitting. A validation step acts as a quality gate. 98.6% of 2,034 chunks pass, and the failures are duplicates. The service is a FastAPI app with typed config via Pydantic Settings, JSON logging, and Docker. Next is embedding with sentence-transformers into ChromaDB, retrieval, and generation.

**Q: Why RAG instead of fine-tuning?**
> Docs change often; with RAG I just re-ingest instead of retraining. RAG gives citations, so answers are verifiable. It's much cheaper, and it reduces hallucination because the model answers from retrieved text. Fine-tuning is better for teaching *style/format*, not for injecting fresh facts.

**Q: How did you choose a chunking strategy?**
> Fixed-size chunking cuts code and sentences in half. I split along the document's own structure (headings) first, merged small sections up to a target size, and for oversized sections fell back through progressively finer boundaries: code blocks, headings, paragraphs, lines, sentences, then characters. For the size limit, I measured instead of guessing. My embedding model truncates at 256 tokens, and running its tokenizer over my chunks showed 47% of the text was being silently ignored, because Boto3 docs are identifier-heavy at about 3 chars per token. I cut the max to 700 chars, which brought the ignored share to 0.3%. Next I'd validate it with retrieval metrics like Recall@k.

**Q: How do you handle code in the docs?**
> html2text damages code formatting, so I extract code containers first, replace them with unique placeholders, convert the rest, then restore the code as fenced Markdown blocks with a detected language. Code blocks are also stored separately in chunk metadata.

**Q: How do you make the crawler responsible / robust?**
> Rate limiting at 2 req/s, a descriptive User-Agent, domain restriction, exclude patterns, a page budget, a visited set to avoid cycles, per-page exception handling so one failure doesn't stop the run, and priority ordering so the budget goes to the most valuable pages. Improvements: retries with exponential backoff, respecting robots.txt, and fetching each page once (it currently fetches twice).

**Q: How do you ensure data quality?**
> A validator checks length bounds, required metadata, header-only or whitespace chunks, navigation-heavy pages, tiny code blocks, and duplicates via content fingerprints, and produces a report with pass rate and size distribution. It runs before embedding so junk never reaches the index.

**Q: Why deterministic chunk IDs?**
> Hashing URL + section + content prefix gives the same ID across runs, so loading into the vector DB is an upsert: re-running ingestion updates instead of duplicating. The pipeline is idempotent.

**Q: Which embedding model and why?**
> all-MiniLM-L6-v2: small (~80 MB), fast on CPU, 384 dimensions, good quality for its size, free and local, so no data leaves the machine. Trade-off: short input window and general-domain training. I'd benchmark alternatives like bge-small or a code-aware model using Recall@k on a labelled question set.

**Q: Why ChromaDB?**
> Simple, embedded (no separate server), persists to disk, supports metadata filtering, fine for tens of thousands of chunks. At larger scale or multi-instance deployment I'd look at pgvector, Qdrant or a managed service.

**Q: How will you evaluate it?**
> Separately for retrieval and generation. Retrieval: a labelled set of questions with the doc page that should be found; measure Recall@5 and MRR. Generation: faithfulness (is every claim supported by retrieved context?), answer relevance, and whether the code actually runs (could test against moto, a library that mocks AWS).

**Q: What design patterns did you use?**
> Template Method / abstract base class for scrapers (crawl loop in the base, site-specific parsing in subclasses), dataclass as a data transfer object for chunks, singleton settings, and the FastAPI lifespan for resource management.

**Q: What would you do differently / what's the biggest weakness?**
> Pick 2–3 items from §10: double fetching, chunk size vs. model token limit, no automated tests yet. Then say how you'd fix each. Interviewers love this.

**Q: Why JSON logging? Why Pydantic Settings? Why FastAPI?**
> JSON logs are machine-queryable in log platforms. Pydantic Settings gives typed, validated, environment-driven config (12-factor). FastAPI is async, uses type hints for validation, and auto-generates OpenAPI docs.

---

## 13. Glossary

| Term | Meaning |
|---|---|
| **RAG** | Retrieval-Augmented Generation: fetch relevant documents, give them to the LLM as context |
| **Chunk** | A small piece of a document, the unit we embed and retrieve |
| **Embedding** | A list of numbers (vector) representing meaning; similar texts give nearby vectors |
| **Cosine similarity** | Measures the angle between two vectors; 1 = same direction (similar meaning) |
| **Vector DB** | Database optimised for "find the nearest vectors" (ChromaDB, FAISS, Pinecone) |
| **Top-k** | Return the k most similar chunks |
| **Token** | A unit of text for models (~4 English characters on average) |
| **Context window** | Max tokens an LLM can read at once |
| **Hallucination** | Model states something plausible but false |
| **Idempotent** | Running it twice gives the same result as once |
| **Rate limiting** | Capping requests per second |
| **ABC** | Abstract Base Class: a class that defines methods subclasses must implement |
| **ASGI / uvicorn** | Async Python web server interface / the server that runs FastAPI |
| **CORS** | Browser rule controlling which websites may call your API |
| **Boto3** | AWS's official Python SDK |
| **IAM policy** | JSON document saying which AWS actions are allowed on which resources |
| **Sphinx** | Python documentation generator; Boto3 docs are built with it (hence `div.body`, `highlight-python`) |

---

## 14. Hands-on exercises

Do these yourself. They're the fastest way to really understand the code.

1. **Trace one page.** In a Python shell:
   ```python
   from src.rtfm_agent.ingestion.boto3_scraper import Boto3Scraper
   s = Boto3Scraper()
   p = s.get_page_content("https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3/client/put_object.html")
   print(p["content"][:1500])
   chunks = s.chunk_content(p["content"], {"url": p["url"], "title": p["title"]})
   for c in chunks: print(len(c.content), c.section)
   ```
   Questions: where does each chunk start and end? Why?
2. **Read the fixes for bugs #1 and #2** with `git diff`, then *break* one on purpose (e.g. delete the cache check in `_fetch_html`) and watch `test_each_page_is_downloaded_once` fail. That's how you learn what a test protects.
3. **Read `tests/test_chunking.py`**, then add one test of your own, e.g. "a 3,000-char code block followed by prose: no chunk exceeds `settings.CHUNK_MAX_TOKENS`".
4. **Measure truncation yourself**: set `CHUNK_MAX_TOKENS=300` in `.env`, run `rechunk_pages()` (no network needed), then `python scripts/build_index.py --rebuild` and watch the "Token check" line report chunks over 256.
5. **Break the placeholder trick**: what happens if the docs themselves contain the text `__CODEBLOCK_0__`? How would you make it safer? (Hint: random/UUID tokens.)
6. **Explain out loud** (or to a friend) the RAG diagram in §3 in under 60 seconds.
