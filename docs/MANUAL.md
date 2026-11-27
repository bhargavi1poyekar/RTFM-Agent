# RTFM Agent: Learning Manual

A walkthrough of what has been built, how each piece works, why it was designed that way, and how to talk about it in an interview.

> Status snapshot as of 27 Nov 2026. Update section 2 as you build more.

---

## Table of contents

1. [What is this project?](#1-what-is-this-project)
2. [Where are we? (progress tracker)](#2-where-are-we-progress-tracker)
3. [The big picture: how RAG works](#3-the-big-picture-how-rag-works)
4. [Project layout](#4-project-layout)
5. [Part A: Project foundation (config, logging, API, tooling)](#5-part-a-project-foundation)
6. [Part B: The ingestion pipeline (the real work so far)](#6-part-b-the-ingestion-pipeline)
7. [Part C: Data quality validation](#7-part-c-data-quality-validation)
8. [Part D: Embeddings and the vector store](#8-part-d-embeddings-and-the-vector-store)
9. [Part E: Retrieval and evaluation](#9-part-e-retrieval-and-evaluation)
10. [Part F: Generation, the IAM check and the API](#10-part-f-generation-the-iam-check-and-the-api)
11. [How to run everything](#11-how-to-run-everything)
12. [Health check results (what works, what doesn't)](#12-health-check-results)
13. [Known issues and improvements](#13-known-issues-and-improvements)
14. [What's next: the roadmap and concepts to learn](#14-whats-next)
15. [Interview prep: questions and model answers](#15-interview-prep)
16. [Glossary](#16-glossary)
17. [Hands-on exercises](#17-hands-on-exercises)

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
| 1 | **Ingestion**: crawl Boto3 docs, HTML→Markdown, preserve code, chunk | ✅ Done (design items in §13 remain) | `src/rtfm_agent/ingestion/base.py`, `boto3_scraper.py` |
| 2 | **Validation**: data-quality checks on chunks | ✅ Done | `src/rtfm_agent/ingestion/validator.py` |
| 3 | Dataset: **2,800 pages** of the 10 focus services → 61,582 chunks, **32,176 unique** (the rest are true duplicates). **Coverage test**: every focus service has ≥ 98% of its operations (checked against `botocore`). | ✅ Re-scraped 26 Nov, re-chunked 27 Nov (gitignored, 59 MB) | `data/raw/pages.json`, `chunks.json`, `ingestion/coverage.py` |
| 4 | Automated tests (pytest) | ✅ 126 offline tests | `tests/` |
| 4b | Chunk size: **102 tokens** (128-token budget, the length the embedding model was trained on) | ✅ 27 Nov | `config/settings.py` |
| 5 | **Embedding**: all-MiniLM-L6-v2, 384-dim, with a URL breadcrumb per chunk | ✅ Done | `src/rtfm_agent/embedding/embedder.py` |
| 6 | **Vector store**: ChromaDB, cosine distance, idempotent upserts | ✅ 32,176 chunks, 0% truncated (255 MB, gitignored) | `embedding/vector_store.py`, `scripts/build_index.py` |
| 7 | **Retrieval**: vector search, one chunk per page (BM25 / weighted hybrid available) | ✅ Done | `src/rtfm_agent/retrieval/retriever.py` |
| 8 | **Retrieval evaluation**: 53 labelled questions (36 dev / 17 test), Recall@k, MRR | ✅ Dev: right page first 78%, top-5 94%, MRR 0.837 | `eval/retrieval_questions.json`, `scripts/eval_retrieval.py` |
| 9 | **Generation**: prompt with numbered sources, parse code / IAM policy / citations | ✅ Ollama `llama3.1:8b` (OpenAI / Anthropic switchable) | `src/rtfm_agent/generation/` |
| 10 | **IAM grounding check**: every policy action checked against the retrieved docs | ✅ Done | `generation/iam_check.py` |
| 11 | API endpoint `POST /api/v1/query` | ✅ Done (503 if the LLM is down) | `src/rtfm_agent/api/query.py` |
| 12 | Generation evaluation | 🟡 First pass: 8 of 12 dev answers fully correct | `scripts/eval_generation.py` |
| 13 | Conversation memory (multi-turn) | ❌ Not started | |
| 14 | Frontend (Streamlit), CI (GitHub Actions), cloud deploy | ❌ Not started | |

**Honest summary:** roughly **65–70% done**. The full RAG loop works end to end: scrape → chunk → embed → store → retrieve → generate → check, behind an API, with measured quality at each step. What's left is mostly making it better (fresh evaluation questions, code execution checks, faster answers) and shipping it (memory, UI, CI, deploy).

---

## 3. The big picture: how RAG works

**RAG = Retrieval-Augmented Generation.** Two phases:

```
 OFFLINE (run once / on a schedule)
 ┌────────────┐   ┌──────────────┐   ┌──────────┐   ┌──────────┐   ┌─────────────┐
 │ Boto3 docs │──▶│ Scrape+clean │──▶│  Chunk   │──▶│  Embed   │──▶│ Vector DB   │
 │  (HTML)    │   │ HTML→Markdown│   │102 tokens│   │ (MiniLM) │   │ (ChromaDB)  │
 └────────────┘   └──────────────┘   └──────────┘   └──────────┘   └─────────────┘
                     ✅ done           ✅ done        ✅ done        ✅ done

 ONLINE (every user question)
 ┌──────────┐   ┌──────────┐   ┌──────────────┐   ┌────────────┐   ┌──────────────┐   ┌───────────┐
 │ Question │──▶│  Embed   │──▶│ Search top-5 │──▶│ Prompt LLM │──▶│ Answer + code│──▶│ IAM check │
 │          │   │ question │   │ 1 per page   │   │ with chunks│   │ + policy+cites│  │ vs. docs  │
 └──────────┘   └──────────┘   └──────────────┘   └────────────┘   └──────────────┘   └───────────┘
                    ✅              ✅                  ✅                 ✅               ✅
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
│   ├── main.py            # FastAPI app (/health, /, includes the query router)
│   ├── ingestion/
│   │   ├── base.py        # DocumentChunk dataclass + BaseScraper (abstract)
│   │   ├── boto3_scraper.py  # Concrete scraper for Boto3 docs
│   │   ├── tokens.py      # count_tokens(): the embedding model's own tokenizer
│   │   ├── validator.py   # Chunk quality checks + report
│   │   └── coverage.py    # Scraped pages vs. botocore's operation list
│   ├── embedding/
│   │   ├── embedder.py    # SentenceTransformer wrapper (lazy load, normalised)
│   │   └── vector_store.py   # ChromaDB: breadcrumbs, metadata, add/search
│   ├── retrieval/
│   │   ├── retriever.py   # vector / BM25 / hybrid (RRF), one chunk per page
│   │   └── evaluation.py  # Recall@k, MRR over labelled questions
│   ├── generation/
│   │   ├── llm.py         # Ollama / OpenAI / Anthropic behind one interface
│   │   ├── prompt.py      # Prompt with numbered sources + answer parser
│   │   ├── iam_check.py   # Grounding check for IAM policy actions
│   │   └── agent.py       # retrieve → prompt → LLM → parse → check
│   └── api/query.py       # POST /api/v1/query
├── scripts/
│   ├── test_scraper.py    # Manual smoke test: scrape 1 page (or 5 with --full)
│   ├── batch_scrape.py    # Real run: crawl up to N pages + validate
│   ├── inspect_chunks.py  # Print stats about chunks.json
│   ├── build_index.py     # Embed chunks into ChromaDB (+ truncation check)
│   ├── eval_retrieval.py  # Score retrieval configs on the question set
│   ├── eval_generation.py # Score full answers (code, policy, citations)
│   └── ask.py             # Ask a question from the terminal
├── eval/retrieval_questions.json  # 53 labelled questions (dev/test split)
├── data/                  # all gitignored
│   ├── raw/pages.json     # cleaned page text (re-chunk without re-scraping)
│   ├── raw/chunks.json    # chunks (61,582, incl. duplicates)
│   ├── embeddings/chromadb/  # the vector index (32,176 chunks)
│   └── experiments/       # side-by-side indexes and backups
├── requirements/          # base / dev / prod dependency split
├── tests/                 # 126 offline tests
├── docs/                  # this manual + the blog series
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
- `lifespan` context manager: code before `yield` runs at startup, after runs at shutdown.
- **CORS middleware**: lets a browser frontend on another domain call the API.
- `GET /health`: health check. Load balancers / Cloud Run / Kubernetes call this to know if the container is alive.
- `GET /`: welcome message. FastAPI auto-generates interactive docs at `/docs` (Swagger UI).
- `POST /api/v1/query`: the real endpoint, see §10.

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

**Why a deterministic hash for `chunk_id`?** `make_chunk_id()` = `md5(url + full content)` gives the same ID every time you scrape the same content. When we load into ChromaDB, re-running ingestion can skip or update existing chunks instead of creating duplicates. This makes the pipeline **idempotent** (safe to run twice). md5 here is for identity, not security, so it's fine.

**Bug story:** the ID used to hash only the *first 100 characters*. Templated docs often start two chunks on the same page the same way, so **227 chunks shared an ID** and would have silently overwritten each other in the database. Same lesson as the duplicate detector (§7): a prefix is not the content.

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

**Trade-off:** too small = chunks lose context ("it returns a dict"… what does?). Too big = retrieval is imprecise, wastes tokens, and **anything past the embedding model's input limit is silently ignored**. Here: **min 20, max 102 tokens**, counted with the embedding model's own tokenizer (`src/rtfm_agent/ingestion/tokens.py`), set once in `config/settings.py` (`CHUNK_MIN_TOKENS`, `CHUNK_MAX_TOKENS`) and used by both the chunker and the validator.

**Why 102 tokens (current):** `all-MiniLM-L6-v2` *truncates* at 256 tokens but was *trained* on sequences of 128 (its model card says so). It also turns text into one vector by averaging every token (mean pooling), so one important sentence in a long chunk gets diluted. A reader of Part 5 measured it: "stop an EC2 instance" scored 0.709 against the sentence alone, 0.594 inside a 128-token chunk and 0.468 inside a 254-token chunk. Budget: 128 − 2 (`[CLS]`/`[SEP]`) − 24 (longest breadcrumb, 23 tokens + blank line) = **102**. Measured on the dev questions (§9):

| Vector + one chunk per page | 230-token budget | **128-token budget** |
|---|---|---|
| Right page ranked first | 63.9% | **77.8%** |
| Right page in top 5 | 97.2% | 94.4% |
| MRR | 0.789 | **0.837** |
| 8B answers fully correct (12 questions) | 7 | **8** |
| Chunks in the index | 16,808 | 32,176 |

The old 230-token index and chunks are kept in `data/experiments/backup_230/`.

**Previously: why 230 tokens:** the model reads 256 tokens, 2 of which are `[CLS]` and `[SEP]` (so 254 are left), minus 24 for the breadcrumb = 230, so chunk + breadcrumb always fits. A reader of the Part 3 blog post had pointed out that characters are only a proxy: chars/token has a median of 3.7 but drops to ~2.3 for the bottom 1% and 1.34 for EC2 instance-type lists, where 700 chars was 519 tokens. Measuring the real unit fixes that for every chunk. Gotcha: the tokenizer maps any single "word" over 100 chars to one `[UNK]` token.

**Lesson across all three versions:** fit the chunk size to the model, and measure in the model's own unit. First the *reading limit* (700 chars → 230 tokens), then the *training length* (102 tokens).

**History: how 700 characters was chosen first (measured, but still a proxy):** `all-MiniLM-L6-v2` truncates at 256 tokens. Running its tokenizer over the old chunks showed Boto3 docs average **3.2 chars/token** (2.8 for code-dense text, since identifiers like `put_bucket_lifecycle_configuration` split into many tokens). 256 × 2.8 ≈ 700. Before/after on real pages:

| | Old (400–2,000 chars) | New (150–700 chars) |
|---|---|---|
| Median chunk | 493 tokens | 220 tokens |
| Chunks over 256 tokens | 80% | 7% (by ≤ 28 tokens) |
| Text the model never sees | **47%** | **0.3%** |

**Strategy: structure-aware chunking (not naive fixed-size):**
1. `_extract_section_hierarchy`: split the markdown at `##` headings. `###` sub-headings are folded into their parent section (bolded). "Note/Warning/Tip" boxes stay in their section rather than becoming tiny standalone chunks.
2. Walk the sections with a **buffer**:
   - **Section (including its `## heading`) > max tokens** → flush buffer, then split the section with `_split_large_section`. (The heading used to be added *after* the size check, which produced over-limit chunks.)
   - **Fits in buffer** → append (merge small neighbouring sections together).
   - **Doesn't fit** → flush the buffer as a chunk, start a new one.
   - Chunks under the min are dropped (usually fragments/noise).
   - Every size check measures the *exact* text that will be stored, in tokens.
3. `_split_large_section` uses a **cascading/recursive split**, gentlest first:
   1. around code blocks (never cut a code block in half if avoidable)
   2. at markdown headings
   3. at paragraphs (`\n\n`)
   4. at lines (`\n`)
   5. at sentences (`. `)
   6. hard cut (last resort): `_hard_split` binary-searches the longest prefix that fits in tokens, prefers a space near the end, and never drops text

   This is the same idea as LangChain's `RecursiveCharacterTextSplitter`, written by hand. Be ready to say: *"I split at the most semantically meaningful boundary that still respects the size limit."*
   **Recursion safety:** each recursive call must move to a *finer* strategy, or it loops forever. Two old bugs broke that (a paragraph right after a code block, and text containing `\n##x`); both raised `RecursionError` and the page was silently skipped. Fixed by stripping the re-added separator before recursing, and by checking for headings with the same regex used to split on them.
4. `_deduplicate_chunks`: drop chunks whose **whole** text (whitespace-normalised) matches an earlier chunk on the same page. It used to compare only the first 100–300 characters, which wrongly dropped different sections that share boilerplate openings.

There's also an unused `_fixed_size_chunking` fallback (1,000 chars with 100 overlap). **Overlap** means consecutive chunks share some text so a sentence cut at a boundary still appears whole in one of them.

### 6.4 Output

- `data/raw/pages.json`: the cleaned text of every page (2,800 pages, 20 MB). Changing the chunk size only needs `rechunk_pages()`, not a 25-minute re-crawl.
- `data/raw/chunks.json`: a list of chunk dicts (61,582 chunks, 59 MB, including duplicates; filter with `ChunkValidator().filter_valid()`).

Both are gitignored (generated data). Rebuild with `python scripts/batch_scrape.py --max-pages 2800`.

**Coverage check (`ingestion/coverage.py`, `tests/test_coverage.py`):** compares the scraped `<service>/client/<operation>` pages with `botocore`'s operation list for each focus service, and fails under 95%. Added after the 2,300-page scrape quietly stopped EC2 alphabetically at `describe_vpn_concentrators` (484 of 830 operations: no `run_instances`, `stop_instances`, `terminate_instances`). Run on that old scrape, the test fails on EC2 (58%); the current 2,800-page scrape passes (98–100% for every service). Idea from a reader of blog Part 6.

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

**Current results** on `chunks.json` (2,800 pages, 102-token chunks):
```
Total 61,582 | Valid 32,176 (52.2%) | Invalid 29,406 (all "duplicate")
sizes (chars): min 33, median 288, avg 274, max 1,104
2,945 chunks contain code (3,068 code blocks) | 2,798 unique source pages
```
A 52% pass rate sounds alarming, but every rejection is a *true* duplicate: Boto3 docs repeat the same blocks on many pages (e.g. EC2's list of taggable resource types appears on 107 pages). Smaller chunks mean more of these blocks become identical chunks. Note the validator *checks* size in tokens but still *reports* sizes in characters.

---

## 8. Part D: Embeddings and the vector store

**Embedding** (`embedding/embedder.py`): `all-MiniLM-L6-v2` turns any text into **384 numbers** that capture its meaning; similar meaning → nearby vectors. It's small, free and runs locally (nothing leaves the machine). The model loads lazily on first use, embeds in batches, and **normalises** vectors (length 1) so cosine similarity is just a dot product.

**What gets embedded** (`vector_store.document_text`): a breadcrumb line + the chunk text:
```
s3 > client > put_object

Returns a dictionary with the following keys...
```
Why: of 16,697 chunks, **16,212 had the section label "Overview"**, which says nothing. The URL (`.../services/s3/client/put_object.html`) says exactly where a chunk comes from, so `parse_doc_url()` turns it into `service / page_type / page` and `context_header()` builds the breadcrumb. Even a tiny out-of-context chunk now "knows" which API it belongs to. This technique is called *contextual chunk headers*.

**Vector store** (`embedding/vector_store.py`): ChromaDB `PersistentClient` at `data/embeddings/chromadb`, collection with **cosine distance** (`hnsw:space`). Per chunk it stores the vector, the text, and flat metadata (`source_url`, `title`, `service`, `page_type`, `page`, `has_code`, …) for citations and filtering (e.g. `where={"service": "s3"}`). `search()` returns `score = 1 − distance`.
- **Idempotent:** `add_chunks()` computes IDs with `make_chunk_id`, drops duplicates in the input, and skips IDs already stored. A crashed build can simply be re-run.
- **Privacy:** Chroma's anonymous telemetry is off (`anonymized_telemetry=False`). A harmless posthog version-mismatch error it still logs is silenced.

**`scripts/build_index.py`:** load `chunks.json` → `filter_valid` → report truncation (tokens of breadcrumb + chunk + 2 vs the 256 limit) → embed and store. Current index: **32,176 chunks, 0 over the limit, built in ~20 min on CPU.** Options: `--rebuild`, `--search "query"`, `--persist-dir` (build a side-by-side experiment index), `--min-tokens` / `--max-tokens`.

**Real searches** (no shared words needed): "send a text message to a phone number" → `sns > client > publish` first. A known miss: "turn off a virtual machine" → CloudFormation pages, because AWS calls VMs "instances" and the small model doesn't know AWS slang.

---

## 9. Part E: Retrieval and evaluation

**Retriever** (`retrieval/retriever.py`), three modes:
- `vector` (**default**): meaning search through the vector store.
- `bm25`: classic keyword search (Okapi BM25, k1 = 1.5, b = 0.75, written by hand; ~40 lines). Rewards rare matching words, penalises long documents.
- `hybrid`: both, merged with **Reciprocal Rank Fusion**: `score = Σ weight / (k + rank)`. RRF only uses ranks, so the two very different score scales never need calibrating. `rrf_k` and `bm25_weight` are configurable.

Then **one chunk per page**: big pages produce many near-identical chunks that crowd the top 5, so only the best chunk from each page is kept. Optional `service_filter` (search only the service named in the question) gave no gain and is off.

**Evaluation** (`retrieval/evaluation.py`, `scripts/eval_retrieval.py`): 53 hand-labelled questions in `eval/retrieval_questions.json`, each with the doc page(s) that answer it.
- **Recall@1 / Recall@5:** share of questions with the right page first / in the top 5 (the LLM gets the top 5).
- **MRR:** average of 1/rank of the first right page (1.0 = always first).
- **Dev / test split** (36 / 17, every 3rd question is test). Tune on dev; look at test **once**, at the end. Otherwise you tune to your own exam (*overfitting to the eval set*, a form of data leakage).

**What the evaluation found:**
1. **A data hole.** Several questions scored zero because the pages didn't exist: the 2,300-page budget had stopped EC2 at `describe_vpn_concentrators`. Re-scraped with 2,800 pages; now guarded by the coverage test (§6.4).
2. **One chunk per page** helped consistently (all 53: top-5 92.5% → 96.2%).
3. **Hybrid won on dev but lost on test** (230-token index, right page first: dev 64% → 72%, test 71% → 53%). Keywords grab surface words: "AMI" → `create_image`, "SSH" → `get_ssh_public_key`, "item" → `update_item`. Caveat (from a reader of blog Part 6): on 17 questions, 71% → 53% is **three questions**. The consistent failure pattern is what makes it believable.
4. **128-token chunks** (§6.3d): right page first on dev 64% → 78%. With them, plain hybrid is worse than vector even on dev.
5. **Weighted RRF** (BM25 weight 0.1, on the 128 index): MRR 0.859 vs 0.837 for vector, but that's **one question** on dev after trying 8 settings, so it isn't the default. It needs fresh questions to prove itself.

**Current default (vector + one per page, 128-token index):** dev right page first **77.8%**, top-5 **94.4%**, MRR **0.837**.

**Honest caveats:** the test split has been looked at, so it's no longer a clean held-out set. The same person wrote the questions and the retriever. The next round needs fresh questions written by someone else, ideally readers.

---

## 10. Part F: Generation, the IAM check and the API

**Pipeline** (`generation/agent.py`, `RTFMAgent.ask`):
```
question → retrieve top 5 → build prompt → LLM → parse → IAM check → Answer
```
If retrieval finds nothing, the LLM isn't called at all.

**LLM interface** (`generation/llm.py`): `get_llm()` returns an object with one method, `complete(system, user)`, for **Ollama, OpenAI or Anthropic** (`LLM_PROVIDER` in `.env`). Client libraries are imported only for the provider in use. A missing API key raises `LLMError` with a clear message. Any provider failure (server down, model not pulled) becomes an `LLMError` too, which the API turns into a 503.
- **Ollama gotcha:** if a prompt is longer than the context window, Ollama silently drops the **start** of it, which is where the rules are. Prompts measure ~900–1,300 tokens, so `OLLAMA_NUM_CTX` is set explicitly to 4096 instead of relying on a version-dependent default.

**Prompt** (`generation/prompt.py`): the chunks become numbered sources `[1] <url>\n<text>`. The system prompt says: use only methods / parameters / IAM actions from the excerpts; if they don't answer it, reply with one exact sentence (`NOT_FOUND`); cite inline like `[2]`; reply as a short explanation + one ```python block + one ```json least-privilege IAM policy.

**Parser:** pulls out the code, the policy and the cited numbers, with warnings for: no code, no policy, invalid JSON, **JSON that isn't a policy** (no `Statement`; small models sometimes paste a sample API response there), citations to sources that don't exist, and no citations at all. Citations are read from the prose only, so `items[0]` in code isn't mistaken for source 0.

**IAM grounding check** (`generation/iam_check.py`; idea from a reader of blog Part 1): one invented action in a confident policy is much harder to spot than broken Python, so every action gets a verdict:

| Verdict | Meaning | Example |
|---|---|---|
| `grounded` | supported by a retrieved page: the page *is* that operation, or names the action | `ec2:RunInstances` ← `ec2/client/run_instances` |
| `in_docs` | real, but not in what was retrieved (not cited evidence) | `iam:PassRole` for a launch question |
| `not_found` | no such operation or mention anywhere in the docs: **likely invented** | `s3:downloadFile`, `s3:GeneratePresignedUrl` |
| `too_broad` | a wildcard | `s3:*` |

Rules learned the hard way:
- **Boto3 helpers aren't API calls.** `upload_file` has its own docs page, but there's no `s3:UploadFile` action; it needs `s3:PutObject`. `BOTO3_HELPERS` maps helpers to the permissions they really need.
- **IAM ignores case, not underscores.** `ec2:terminateInstances` is valid; `ec2:describe_instances` is not.
- **Known limit:** IAM names usually match API names, but not always (`list_objects_v2` needs `s3:ListBucket`; `s3:CopyObject` isn't a real action, copying needs `GetObject` + `PutObject`). Such mismatches pass or fail only by name. Treat `not_found` as "check this", not proof.

**API** (`api/query.py`): `POST /api/v1/query` with `{"question": "...", "top_k": 5}` (question 3–1,000 chars, top_k 1–10). Returns the answer, code, policy, the IAM verdicts, the sources (with `cited` flags), warnings, model and seconds. The agent is built once on the first request (it loads the model and index). The handler is a plain `def`, so FastAPI runs the slow LLM call in a worker thread.

**Model comparison** (`scripts/eval_generation.py`, 12 dev questions, one run each, after fixing the checker):

| | llama3.2:3b | llama3.1:8b | 8b + 128-token chunks |
|---|---|---|---|
| Calls the right method | 10 | 11 | **12** |
| Cites the right page | 8 | 11 | 11 |
| Every policy action grounded | 7 | 7 | 8 |
| **Fully correct** (method + citation + grounded policy) | 5 | 7 | **8** |
| Median seconds (CPU, 16 GB, no GPU) | 60 | 115 | 102 |

Default: **`llama3.1:8b`**. Small sample and temperature 0.3, so differences of one question are noise.

---

## 11. How to run everything

Always activate the venv first (Windows PowerShell: `venv\Scripts\activate`; Git Bash: `source venv/Scripts/activate`). Run from the project root.

| Task | Command |
|---|---|
| Start API | `python run.py` → open http://localhost:8000/docs |
| Smoke-test scraper on 1 page | `python scripts/test_scraper.py` |
| Scrape 5 pages | `python scripts/test_scraper.py --full` |
| Full crawl + validation | `python scripts/batch_scrape.py --max-pages 2800` (~25 min; overwrites `pages.json` and `chunks.json`) |
| Re-chunk without crawling | `python -c "from src.rtfm_agent.ingestion.boto3_scraper import Boto3Scraper; Boto3Scraper().rechunk_pages()"` |
| Inspect chunks | `python scripts/inspect_chunks.py` |
| Build the vector index | `python scripts/build_index.py --rebuild` (~20 min on CPU) |
| Try a search | `python scripts/build_index.py --search "upload a file to s3"` |
| Score retrieval | `python scripts/eval_retrieval.py` (dev) / `--split all` / `--show-misses` |
| Ask a question (needs Ollama running) | `python scripts/ask.py "How do I upload a file to S3?"` |
| See the prompt without calling the LLM | `python scripts/ask.py --sources-only "..."` |
| Score full answers | `python scripts/eval_generation.py --model llama3.1:8b --limit 12` (~25 min) |
| Run the tests | `python -m pytest` |
| Readable logs | set `LOG_FORMAT=text` in `.env` |

**Ollama setup (once):** install from ollama.com, then `ollama pull llama3.1:8b` (and `llama3.2:3b` for a faster, weaker option). In `.env`: `LLM_PROVIDER=ollama`, `LLM_MODEL=llama3.1:8b`. It listens on `localhost:11434` only.

**Offline runs:** set `HF_HUB_OFFLINE=1` so the embedding model and tokenizer load from the local cache without contacting Hugging Face (the tests do this automatically in `tests/conftest.py`).

---

## 12. Health check results

Checked on 27 Nov 2026:

| What | Result |
|---|---|
| Tests (`python -m pytest`) | ✅ 126 passed in ~40 s, all offline |
| Coverage test on the current scrape | ✅ every focus service 98–100% of its operations |
| Index | ✅ 32,176 chunks, 0% truncated |
| Retrieval (dev) | ✅ right page first 77.8%, top-5 94.4%, MRR 0.837 |
| End-to-end answer via Ollama `llama3.1:8b` | ✅ ~100 s per answer on CPU |
| `POST /api/v1/query` with Ollama stopped | ✅ clean 503 with the reason |

Earlier checks (25 Nov 2026):

| What | Result |
|---|---|
| Python venv (3.11.0) & core deps (FastAPI, bs4, html2text, pydantic-settings) | ✅ import fine |
| FastAPI app: `/health` and `/` | ✅ 200 OK, startup/shutdown logs fire |
| Live scrape of 1 Boto3 page (SQS `send_message`) | ✅ 8 chunks, code block detected, ~0.8s |
| Validator on existing data | ✅ 98.6% pass rate |
| ChromaDB import | ✅ 0.5.0 |
| **PyTorch / sentence-transformers import** | ✅ Fixed. Was `OSError: [WinError 126] ... fbgemm.dll`. Reading `fbgemm.dll`'s import table showed it needs `libomp140.x86_64.dll`, which the **torch 2.4.0 Windows wheel doesn't ship**. Upgraded to torch 2.4.1 (pinned in `requirements/base.txt`), and installed torchvision 0.19.1 to match it, replacing an incompatible 0.29 that wasn't in requirements. Debugging lesson: "module could not be found" names the DLL that failed to load, not the missing dependency, so list its imports. |
| Tests (`python -m pytest -v`) | ✅ 42 passed in ~1 s (at the time) |

---

## 13. Known issues and improvements

These are real findings from reading the code. Knowing your own project's weaknesses is a **strength** in interviews ("what would you do differently?").

**Bugs (✅ all fixed 25 Nov 2026, covered by `tests/test_boto3_scraper.py`)**
1. ✅ **`batch_scrape.py` ignored the multi-seed list.** It built `seeds` but called `scrape()` without `seed_urls=seeds`, so the crawl started only from the main index. Now passes the seeds; a live 3-page run starts at S3 → EC2 → IAM.
2. ✅ **Every page was downloaded twice** (`get_page_content()` and `get_links()` each called `session.get`). Fixed with `_fetch_html()`, which caches the *raw bytes* of the last page. Why bytes and not the parsed `soup`? `get_page_content()` deletes `<nav>` from its soup; sharing it would hide navigation links from `get_links()`. Live check: 3 pages = 3 requests (was 6).
3. ✅ `self._rate_limit()` sat above the docstrings, so they weren't docstrings. Rate limiting now lives in `_fetch_html()`, and only applies to real network calls (a cache hit doesn't wait).
4. ✅ `prioritize_urls()` was called by `BaseScraper` but only defined in `Boto3Scraper`. The base class now has a default (keep order = plain BFS) that subclasses override. That's the "hook method" variant of the Template Method pattern.
- ✅ **Crawler never reached method pages** (100-link cap + path-length priority). See the war story in §6.3a. Fixed with no cap, tiered round-robin priority, and tests for both.
- ✅ Bonus, found by the new test: `batch_scrape` crashed with `ZeroDivisionError` when a run took ~0 s (e.g. network down) while printing pages/sec. Division is now guarded.

**Design / quality**
5. ✅ **Chunk size vs. the embedding model (fixed).** Was 47% of text ignored by the 256-token limit; 0.3% with 700-char chunks (see §6.3 table); **0%** with the token-based limit; now sized to the model's *training* length (102 tokens + breadcrumb + `[CLS]`/`[SEP]` ≤ 128), which also improved retrieval. Cleaned pages are saved to `data/raw/pages.json`, so `rechunk_pages()` can re-chunk without re-crawling. Smaller chunks make *context* more important, so #6 below matters more now. Large code blocks are split across chunks; keeping each code block whole (or giving the LLM the neighbouring chunks) is a possible next improvement.
6. ✅ (worked around at embed time) **Weak `section` labels.** `vector_store.context_header()` now builds a breadcrumb from the URL (`sqs > client > send_message`) and embeds it with each chunk. Original note: Only 24 unique sections across 2,034 chunks ("Client" ×1,236, "Overview" ×374). The page's breadcrumb section is computed in `_get_section()` but thrown away. Better: store it in metadata, and prefix each chunk with its page title + heading before embedding (*contextual chunk headers*), so a chunk saying "Parameters: QueueUrl…" also carries "SQS › send_message".
7. ✅ Thresholds were inconsistent (chunker 400–2,000, validator 400–2,500, `batch_scrape` 200–3,000, and the heading could push a chunk over max). Now one source of truth: `settings.CHUNK_MAX_TOKENS` / `CHUNK_MIN_TOKENS`. (`CHUNK_OVERLAP` is still unused.)
8. 🟡 Queue membership check now uses a `set` (O(1)). Still O(n): `pop(0)` and re-sorting the whole queue every 10 pages. Fine for a few thousand pages; at much larger scale use a heap (`heapq`) keyed by priority.
9. ✅ Dedup (validator *and* scraper) used text prefixes. That counted some chunks twice, made `filter_valid()` disagree with `validate()`, and **discarded different content** that shared boilerplate openings (22 of 33 flagged chunks on a live sample). Now both compare the whole whitespace-normalised text through one shared helper.
10. No retries on network errors. `tenacity` is already in requirements, so wrap `session.get` in `@retry` with exponential backoff.

**Engineering hygiene**
11. ✅ **Tests: 126 passing in ~40 s, all offline.** Run with `python -m pytest -v`.
    - `test_boto3_scraper.py`: crawler with a `FakeSession` (stand-in for `requests.Session` that records URLs): one download per page, links survive `<nav>` removal, code preserved, seeds passed, pages saved for re-chunking.
    - `test_chunking.py`, `test_token_chunking.py`: size limits (in tokens), merging, no text lost, min-size drop, code blocks recorded, deterministic IDs, plus **regression tests** for each bug fixed (heading overshoot, both infinite recursions, prefix dedup). Each was confirmed to fail on the old code.
    - `test_validator.py`: one parametrised test per rule, report numbers, `filter_valid` agrees with `validate`.
    - `test_embedding.py`: URL parsing, breadcrumbs, metadata, colliding IDs, idempotent re-runs, persistence (with a `FakeEmbedder` and a temp Chroma folder).
    - `test_retrieval.py`: BM25, (weighted) RRF, one-per-page, service filter, metrics.
    - `test_generation.py`: prompt, parser, IAM verdicts (incl. helpers and spelling rules), the agent with a `FakeLLM`, provider config errors, and the API (200 / 422 / 503).
    - `test_coverage.py`: coverage logic, plus the real scrape when `data/raw/pages.json` exists (skipped otherwise).
    - `test_api.py`: `/health`, `/`, `/docs` via FastAPI's `TestClient`.
12. ✅ **`.dockerignore` added.** `COPY . .` used to copy `.env` (secrets), `venv/`, `.git/` and data into the image. Gotcha worth knowing: unlike `.gitignore`, a `.dockerignore` pattern such as `__pycache__` only matches at the project root, so nested folders need `**/__pycache__`. `data/embeddings/` is deliberately *not* excluded yet, because whether to ship a prebuilt vector index inside the image is a later decision. Still open: the stage is named `builder` but there's no second stage, so it isn't really multi-stage.
13. CORS `allow_origins=["*"]` with `allow_credentials=True` is too permissive for production. Restrict to the frontend's domain.
14. `pyproject.toml` says `readme = "README.md"` but the file is `Readme.md` (breaks on Linux, which is case-sensitive).
15. ✅ `chunks.json` is no longer tracked (the current one is 59 MB). Generated data belongs outside git; rebuild it with `python scripts/batch_scrape.py --max-pages 2800` (~25 min). `.gitignore` also covers the ChromaDB index folder and `data/experiments/`. The old 4 MB version still exists in git *history*; removing it from history would mean rewriting commits, which isn't worth it at this size.
16. User-Agent still has a placeholder GitHub URL.

**Retrieval and generation**
17. **The evaluation sets are small and partly "used".** 36 dev / 17 test retrieval questions, 12 generation questions, one run each. The test split has been looked at. Next: fresh held-out questions from someone other than the author.
18. **Answers are slow on CPU** (~100 s with `llama3.1:8b`). Options: a GPU, a smaller model (`llama3.2:3b`: 60 s, fewer correct answers), or a hosted API (fast, but questions and doc excerpts leave the machine).
19. **Generated code is never run.** It's checked to parse, not to work. Next: execute it against `moto` (a library that fakes AWS) in a sandbox.
20. **The IAM check matches names, not semantics.** `s3:PutObject` for a *download* is "real" but wrong for that code; API-vs-IAM name mismatches (`s3:CopyObject`) pass. A proper fix needs AWS's Service Authorization Reference (the official list of actions per API call) as ground truth.
21. **The model ignores AWS slang.** "turn off a virtual machine" doesn't find `stop_instances`. Options: query rewriting by the LLM, a synonym list, or a stronger embedding model (measure with the question set).
22. `RETRIEVAL_SCORE_THRESHOLD` (0.7) and `CHUNK_OVERLAP` in `settings.py` are unused. Good answers score as low as ~0.5 with this model, so a 0.7 cut-off would refuse real questions.

---

## 14. What's next

Done since the first version of this list: embeddings, vector store, retriever (with hybrid search as an option), evaluation, generator, API, and the IAM check.

Suggested order now, with the concept you'll learn at each step:

1. **Fresh evaluation questions** (15–20, written by someone else, e.g. blog readers), kept as a new sealed test set. Then re-test weighted hybrid on them. *Learn:* held-out evaluation, small-sample statistics.
2. **Run the generated code** against `moto` (fake AWS) in a sandbox, and add "code actually runs" to `eval_generation.py`. *Learn:* execution-based evaluation, sandboxing untrusted code.
3. **More context for the LLM**: also send each retrieved chunk's neighbours, or the whole section, now that chunks are small. Measure with `eval_generation.py`. *Learn:* the "small to retrieve, big to read" pattern (parent-document retrieval).
4. **Re-ranking** with a cross-encoder over the top 20–30. *Learn:* bi-encoder vs cross-encoder, the precision/latency trade-off.
5. **Memory**: keep the last `MAX_CONVERSATION_TURNS`; rewrite follow-ups ("what about with encryption?") into standalone questions before retrieval (*query rewriting*). The same trick can help AWS slang (§13 #21).
6. **Ship**: Streamlit UI, GitHub Actions CI (lint + tests + coverage test), Docker → Cloud Run.

---

## 15. Interview prep

**Q: Walk me through your project.**
> I built a RAG assistant for AWS's Boto3 SDK. You ask a question; it answers with Python code, a least-privilege IAM policy, and citations to the official docs. A polite crawler collects 2,800 API-reference pages for the 10 most-used services, with a coverage test against botocore's operation list. Pages are cleaned to Markdown with code protected, then cut into structure-aware chunks sized in the embedding model's own tokens. Chunks are embedded locally with MiniLM into ChromaDB, each with a breadcrumb from its URL. Retrieval is vector search with one chunk per page, measured on a labelled question set with a dev/test split: the right page is first 78% of the time and in the top 5 for 94%. A local Llama 3.1 8B via Ollama writes the answer from numbered sources. Then every IAM action in the policy is checked against the retrieved docs, which catches invented actions. It's served by FastAPI with 126 offline tests.

**Q: Why RAG instead of fine-tuning?**
> Docs change often; with RAG I just re-ingest instead of retraining. RAG gives citations, so answers are verifiable. It's much cheaper, and it reduces hallucination because the model answers from retrieved text. Fine-tuning is better for teaching *style/format*, not for injecting fresh facts.

**Q: How did you choose a chunking strategy?**
> Fixed-size chunking cuts code and sentences in half. I split along the document's own structure (headings) first, merged small sections up to a target size, and for oversized sections fell back through progressively finer boundaries: code blocks, headings, paragraphs, lines, sentences, then a hard cut. The size went through three measured versions. First, my embedding model truncates at 256 tokens and 47% of my text was silently ignored, so I cut chunks to 700 characters. Then a reader showed characters are a bad proxy for identifier-heavy text, so I counted the model's own tokens instead (0% truncated). Finally, the model was *trained* on 128 tokens and mean pooling dilutes a key sentence in a long chunk, so I tried a 128-token budget. On my question set, the right page ranked first went from 64% to 78%.

**Q: How do you handle code in the docs?**
> html2text damages code formatting, so I extract code containers first, replace them with unique placeholders, convert the rest, then restore the code as fenced Markdown blocks with a detected language. Code blocks are also stored separately in chunk metadata.

**Q: How do you make the crawler responsible / robust?**
> Rate limiting at 2 req/s, a descriptive User-Agent, domain restriction, exclude patterns, a page budget, a visited set to avoid cycles, one download per page, per-page exception handling so one failure doesn't stop the run, and priority ordering so the budget goes to the most valuable pages. Plus a coverage test, because the budget once ran out halfway through EC2 with zero errors. Improvements: retries with exponential backoff and respecting robots.txt.

**Q: How do you ensure data quality?**
> A validator checks length bounds, required metadata, header-only or whitespace chunks, navigation-heavy pages, tiny code blocks, and duplicates via content fingerprints, and produces a report with pass rate and size distribution. It runs before embedding so junk never reaches the index.

**Q: Why deterministic chunk IDs?**
> Hashing URL + full content gives the same ID across runs, so re-running the load skips what's already stored instead of duplicating. The pipeline is idempotent. It used to hash only a 100-character prefix, and 227 different chunks collided and would have silently overwritten each other.

**Q: Which embedding model and why?**
> all-MiniLM-L6-v2: small (~80 MB), fast on CPU, 384 dimensions, good quality for its size, free and local, so no data leaves the machine. Trade-offs: a short input window (trained on 128 tokens, which drove my chunk size) and general-domain training (it doesn't know "virtual machine" means "instance"). Now that I have a labelled question set, swapping in bge-small or a code-aware model is a measured experiment, not a guess.

**Q: Why ChromaDB?**
> Simple, embedded (no separate server), persists to disk, supports metadata filtering, fine for tens of thousands of chunks. At larger scale or multi-instance deployment I'd look at pgvector, Qdrant or a managed service.

**Q: How do you evaluate it?**
> Separately for retrieval and generation. Retrieval: 53 hand-labelled questions with the doc pages that answer them, split into dev and a held-out test set; I measure Recall@1, Recall@5 and MRR, tune on dev only, and look at test once. Generation: for each answer I check that the code parses and calls the right method, that the policy is a real IAM policy whose actions are grounded in the docs, and that it cites the right page. Next is running the code against moto, a library that fakes AWS. The evaluation also found a data bug: over 40% of EC2's operations had never been scraped.

**Q: Did hybrid search help?**
> I expected it to. BM25 + vector with Reciprocal Rank Fusion raised the right-page-first rate on dev from 64% to 72%, but on the held-out test it dropped from 71% to 53%. Keywords grabbed surface words: "AMI" pulled `create_image` instead of `run_instances`. To be fair, that's three questions out of 17, but every loss had the same cause, so I kept plain vector search and left hybrid as an option. A down-weighted variant looked slightly better on dev, but by one question after trying eight settings, so it needs fresh questions before I'd trust it.

**Q: How do you stop the LLM from hallucinating?**
> Several layers. The prompt contains only numbered doc excerpts and tells the model to use only what's in them and to reply with a fixed "couldn't find it" sentence otherwise; I detect that sentence. Answers must cite sources, and I flag citations to sources that don't exist. And the IAM policy, where a hallucination is most dangerous, gets checked action by action: each one is grounded in a retrieved page, real but not retrieved, likely invented, or a wildcard. On my eval it caught `s3:downloadFile` and `s3:GeneratePresignedUrl`, which don't exist. Its limit: it matches names, so a real-but-wrong permission still passes.

**Q: Why a local LLM?**
> Free, private (nothing leaves the machine), and it fits "start local, upgrade with a measured reason". The cost is speed: about 100 s per answer on CPU. The LLM sits behind a one-method interface, so OpenAI or Anthropic is a config change, and my generation eval tells me what I'd gain.

**Q: What design patterns did you use?**
> Template Method / abstract base class for scrapers (crawl loop in the base, site-specific parsing in subclasses), dataclass as a data transfer object for chunks, singleton settings, a Protocol + factory for LLM providers (strategy pattern), FastAPI dependency injection (which also lets tests swap in a fake agent), and lazy initialisation for the model and index.

**Q: What would you do differently / what's the biggest weakness?**
> Pick 2–3 items from §13: small and partly used evaluation sets, generated code that's never executed, an IAM check that matches names not meaning, slow CPU answers. Then say how you'd fix each. Interviewers love this.

**Q: Why JSON logging? Why Pydantic Settings? Why FastAPI?**
> JSON logs are machine-queryable in log platforms. Pydantic Settings gives typed, validated, environment-driven config (12-factor). FastAPI is async, uses type hints for validation, and auto-generates OpenAPI docs.

---

## 16. Glossary

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
| **Mean pooling** | Turning a text's token vectors into one vector by averaging them; why long chunks dilute a key sentence |
| **BM25** | Classic keyword-ranking formula: rewards rare matching words, penalises long documents |
| **RRF** | Reciprocal Rank Fusion: merges ranked lists by summing 1/(k + rank); needs no score calibration |
| **Recall@k** | Share of questions whose right answer appears in the top k results |
| **MRR** | Mean Reciprocal Rank: average of 1/rank of the first right result |
| **Dev / test split** | Tune on dev; check on test once, so the score isn't something you optimised for |
| **Grounding** | Tying each claim in an answer to retrieved evidence |
| **Ollama** | Runs open LLMs (e.g. Llama) locally behind an HTTP API on `localhost:11434` |
| **Context window (Ollama `num_ctx`)** | Max tokens the LLM reads; Ollama drops the *start* of longer prompts |

---

## 17. Hands-on exercises

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
7. **Read a prompt.** Run `python scripts/ask.py --sources-only "Stop an EC2 instance"` and read exactly what the LLM would see. Which source would you cite?
8. **Catch an invented action.** In a Python shell, call `check_policy()` (§10) with a policy containing `ec2:StopInstances` and `ec2:PowerOff`, using chunks from `Retriever(...).search("Stop an EC2 instance")`. Predict each verdict first.
9. **Find a retrieval miss.** Run `python scripts/eval_retrieval.py --show-misses` and explain *why* one miss happens. Is it the question, the data, or the model?
10. **Write three questions of your own** with their expected pages, and score them. That's how the next held-out test set starts.
