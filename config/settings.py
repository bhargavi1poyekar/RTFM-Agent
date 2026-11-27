# config/settings.py
"""
Configuration management using Pydantic Settings.
This allows us to load config from environment variables, .env files, etc.
"""
from typing import Optional, Literal
from pydantic_settings import BaseSettings
from pydantic import Field
import os

class Settings(BaseSettings):
    # Application
    APP_NAME: str = "RTFM Agent"
    APP_VERSION: str = "1.0.0"
    APP_ENV: Literal["development", "testing", "production"] = "development"
    DEBUG: bool = True
    
    # API Settings
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    API_PREFIX: str = "/api/v1"
    
    # LLM Configuration
    LLM_PROVIDER: Literal["ollama", "openai", "anthropic"] = "ollama"
    LLM_MODEL: str = "llama3"
    LLM_TEMPERATURE: float = 0.3
    LLM_MAX_TOKENS: int = 2000
    OLLAMA_HOST: str = "http://localhost:11434"
    # Ollama silently drops the *start* of a prompt (our rules) if it's longer
    # than the context window. Prompts measure ~900-1,300 tokens at top_k=5;
    # 4096 leaves room for top_k=10 plus the answer.
    OLLAMA_NUM_CTX: int = 4096

    # OpenAI (if using)
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_MODEL: str = "gpt-4o-mini"
    
    # Anthropic (if using)
    ANTHROPIC_API_KEY: Optional[str] = None
    ANTHROPIC_MODEL: str = "claude-3-sonnet-20240229"
    
    # Embedding Configuration
    EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"
    EMBEDDING_BATCH_SIZE: int = 32
    EMBEDDING_DIMENSION: int = 384
    
    # Vector Database
    VECTOR_DB_TYPE: Literal["chromadb", "faiss", "pinecone"] = "chromadb"
    CHROMADB_PERSIST_DIR: str = "data/embeddings/chromadb"
    COLLECTION_NAME: str = "aws_docs"
    
    # Retrieval
    RETRIEVAL_TOP_K: int = 5
    RETRIEVAL_SCORE_THRESHOLD: float = 0.7
    
    # Chunking (sizes in tokens, counted with the embedding model's tokenizer)
    # all-MiniLM-L6-v2 truncates at 256 tokens but was *trained* on 128, and
    # mean pooling dilutes one key sentence across a long chunk. So the
    # budget is 128: minus [CLS]/[SEP] (2) and the breadcrumb such as
    # "s3 > client > put_object" (longest seen: 23 tokens + blank line = 24)
    # leaves 102 for chunk text. Measured on eval/retrieval_questions.json
    # (dev): Recall@1 64% -> 78%, MRR 0.789 -> 0.837 vs the old 230 budget.
    # Characters are not used: chars/token ranges from ~3.7 down to ~1.3.
    CHUNK_MAX_TOKENS: int = 102    # max tokens per chunk
    CHUNK_MIN_TOKENS: int = 20     # smaller chunks are dropped as fragments
    CHUNK_OVERLAP: int = 100
    
    # Memory
    MAX_CONVERSATION_TURNS: int = 5
    MEMORY_STORE_TYPE: Literal["in_memory", "redis"] = "in_memory"
    REDIS_URL: Optional[str] = None
    
    # Rate Limiting
    RATE_LIMIT_PER_USER: int = 100  # requests per hour
    
    # Paths
    DATA_DIR: str = "data"
    RAW_DATA_DIR: str = "data/raw"
    PROCESSED_DATA_DIR: str = "data/processed"
    LOG_DIR: str = "logs"
    
    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True

# Create singleton instance
settings = Settings()

# Ensure directories exist
for dir_path in [
    settings.DATA_DIR,
    settings.RAW_DATA_DIR,
    settings.PROCESSED_DATA_DIR,
    settings.LOG_DIR,
    settings.CHROMADB_PERSIST_DIR,
]:
    os.makedirs(dir_path, exist_ok=True)