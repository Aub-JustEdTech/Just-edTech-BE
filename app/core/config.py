"""
Core configuration settings for Just-EdTech application.
"""

import os
from pathlib import Path

from pydantic import AnyHttpUrl, validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings"""

    # API Configuration
    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "Just-EdTech"

    # Security (SECRET_KEY should be set in .env)
    SECRET_KEY: str
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    ALGORITHM: str = "HS256"
    DEFAULT_TENANT_ID: int = 1  # Default tenant for new users
    DEFAULT_ROLE_ID: int = 2  # Default role (e.g., 'tenant_admin') for new users
    DEFAULT_TENANT_USER_ID: int = 3  # Default user for the default tenant
    # Database (All values should be set in .env)
    POSTGRES_SERVER: str
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_DB: str
    POSTGRES_PORT: str = "5432"
    # Set to "require" for RDS/production; leave empty for local dev without TLS.
    POSTGRES_SSLMODE: str | None = None

    # Redis Configuration (HOST should be set in .env)
    REDIS_HOST: str
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str | None = None
    REDIS_DB: int = 0
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # CORS
    BACKEND_CORS_ORIGINS: list[AnyHttpUrl]
    # RAG Configuration
    CHUNK_SIZE: int = 3000
    CHUNK_OVERLAP: int = 200
    MAX_TOKENS: int = 4000

    # Chatbot Defaults
    CHATBOT_DEFAULT_NAME: str = "New Chatbot"
    CHATBOT_DEFAULT_TITLE: str = "Assistant"
    CHATBOT_DEFAULT_WELCOME_MESSAGE: str = "Hi there! How can I help you today?"
    CHATBOT_DEFAULT_SYSTEM_PROMPT: str = (
        "You are a helpful assistant that answers using the organization's knowledge base."
    )
    CHATBOT_DEFAULT_CHAT_MODEL: str = "openai/gpt-4o-mini"
    CHATBOT_DEFAULT_CHAT_TEMPERATURE: float = 0.7
    CHATBOT_DEFAULT_RAG_TOP_K: int = 3
    CHATBOT_DEFAULT_RAG_MAX_HISTORY: int = 25
    CHATBOT_DEFAULT_RAG_CONTEXT_CHARS: int = 4000
    CHATBOT_DEFAULT_RAG_SNIPPET_CHARS: int = 200
    CHATBOT_DEFAULT_THRESHOLD_VALUE: float = 0.7
    CHATBOT_DEFAULT_BRAND_COLOR: str = "#000000"
    CHATBOT_DEFAULT_PERSONALITY: str = "professional"
    CHATBOT_DEFAULT_CONTACT_LINK: str = ""
    CHATBOT_DEFAULT_SIMILARITY_SCORE: float = 0.5
    CHATBOT_DEFAULT_INPUT_PLACEHOLDER: str = "Ask me anything..."
    CHATBOT_ENABLE_PROMPT_SUGGESTIONS: bool = True
    CHATBOT_DEFAULT_OPENAI_TIMEOUT_S: int = 30
    CHATBOT_DEFAULT_SEARCH_TYPE: str = "similarity"

    # Multimodal RAG Configuration
    CHATBOT_DEFAULT_ENABLE_MULTIMODAL: bool = True
    CHATBOT_DEFAULT_MAX_IMAGES: int = 2
    CHATBOT_DEFAULT_IMAGE_CONTEXT_CHARS: int = 200  # Characters before/after image for context

    # Vector Database Configuration
    VECTOR_STORE_TYPE: str = "qdrant"  # chroma, qdrant, pinecone, weaviate, etc.
    CHROMA_PERSIST_DIR: str = "./chroma_db"
    CHROMA_COLLECTION_PREFIX: str = "tenant"
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_COLLECTION_PREFIX: str = "justedtech"

    # Future: Other vector stores
    PINECONE_API_KEY: str | None = None
    PINECONE_ENVIRONMENT: str | None = None
    WEAVIATE_URL: str | None = None

    # LLM API provider: "openrouter" (default) or "openai"
    LLM_API_PROVIDER: str = "openrouter"
    # OpenRouter (primary when LLM_API_PROVIDER=openrouter)
    OPENROUTER_API_KEY: str | None = None
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    OPENROUTER_HTTP_REFERER: str = "http://localhost:8000"
    OPENROUTER_APP_NAME: str = "Just-EdTech"
    # Concurrency for heatmap chunk classification when OpenRouter's Batch API
    # is unavailable (direct chat-completions path).
    OPENROUTER_BATCH_CONCURRENCY: int = 10
    # Legacy OpenAI direct API (used when LLM_API_PROVIDER=openai, or as
    # fallback key when OPENROUTER_API_KEY is unset).
    OPENAI_API_KEY: str | None = None
    OPENAI_EMBEDDING_MODEL: str = "openai/text-embedding-3-small"

    # Heatmap ingestion overrides. When non-empty, school_scraper-sourced
    # documents use this embedding model + token-mode chunking regardless of
    # the per-tenant chatbot config. Empty = fall back to tenant config.
    HEATMAP_INGEST_EMBEDDING_MODEL: str = "openai/text-embedding-3-small"
    HEATMAP_INGEST_CHUNK_SIZE: int = 500
    HEATMAP_INGEST_CHUNK_OVERLAP: int = 100
    # Doc-level (entity_type/doc_kind/meeting_date) and chunk-level
    # (topics/action_types) classifier settings.
    HEATMAP_INGEST_DOC_CLASSIFIER_MODEL: str = "openai/gpt-4o-mini"
    HEATMAP_INGEST_CHUNK_CLASSIFIER_MODEL: str = "openai/gpt-4o-mini"
    # Max chunks per OpenAI Batch API submission. The API caps at 50,000
    # requests per batch; we use a smaller default to keep batches quick.
    HEATMAP_INGEST_BATCH_SIZE: int = 50_000
    # When True, the heatmap service returns canned sample data instead of
    # reading heatmap_aggregate + Qdrant. Useful for local dev without a
    # populated vector store. Default False (use real data).
    HEATMAP_USE_SAMPLE_DATA: bool = False

    # LangSmith Configuration
    LANGSMITH_TRACING: bool = False
    LANGSMITH_API_KEY: str | None = None
    LANGSMITH_PROJECT: str = "just-edtech-default"

    # Chat Configuration
    CONTEXT_WINDOW_SIZE: int = 10
    CONVERSATION_TITLE_MAX_LENGTH: int = 50
    CONVERSATION_TITLE_WORD_COUNT: int = 7
    MESSAGE_PAGINATION_DEFAULT_LIMIT: int = 50
    CONVERSATION_PAGINATION_DEFAULT_LIMIT: int = 20

    # S3 Configuration (All S3 values should be set in .env)
    S3_BUCKET_NAME: str
    S3_REGION: str
    AWS_ACCESS_KEY_ID: str
    AWS_SECRET_ACCESS_KEY: str

    # Document Processing
    MAX_FILE_SIZE_MB: int = 50
    ALLOWED_DOCUMENT_TYPES: list[str] = [
        ".pdf",
        ".md",
        ".txt",
        ".text",
        ".docx",
        ".doc",
        ".xlsx",
        ".xls",
    ]
    TEMP_UPLOAD_DIR: str = "./temp_uploads"
    IMAGE_STORAGE_DIR: str = "./data/images"
    ENABLE_IMAGE_EXTRACTION: bool = False

    # Bulk upload limits
    BULK_UPLOAD_MAX_FILES: int = 10

    # Avatar/Image Upload Configuration
    MAX_AVATAR_SIZE_MB: int = 5
    ALLOWED_AVATAR_TYPES: list[str] = [
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
    ]

    # Box Integration
    BOX_JWT_CONFIG_PATH: str | None = None
    BOX_ENTERPRISE_ID: str | None = None

    # Agent Configuration
    AGENT_MAX_ITERATIONS: int = 5
    AGENT_MAX_TOKENS_BUDGET: int = 50000
    AGENT_TIMEOUT_SECONDS: int = 120

    # Web Scraping Configuration
    WEB_SCRAPER_TIMEOUT_SECONDS: int = (
        30  # Default timeout in seconds for web scraping requests
    )

    # School Scraper Configuration
    SCHOOL_SCRAPER_USE_PLAYWRIGHT: bool = False
    # User-Agent used by the school scraper for HTTP requests.
    # Default is a curl-style UA because many school-district sites
    # (WordPress + Wordfence/Cloudflare WAFs) block `python-httpx/*` and
    # bot-like UAs with 403, while allowing `curl/*` / `okhttp/*`.
    SCHOOL_SCRAPER_USER_AGENT: str = "curl/8.5.0"
    # Path to a system-installed Chromium binary (e.g. /usr/bin/chromium).
    # Set via env in Docker images that apt-install Chromium instead of
    # letting Playwright download its own copy. Left unset for local dev,
    # where Playwright's own `playwright install chromium` browser is used.
    PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH: str | None = None
    SCHOOL_SCRAPER_MEETING_KEYWORDS: list[str] = [
        "meeting",
        "minutes",
        "archive",
        "archives",
        "minutes archive",
        "minutes archive",
        "board",
        "agenda",
        "governance",
        "committee",
        "records",
        "video",
        "media",
    ]
    SCHOOL_SCRAPER_VIDEO_EXTENSIONS: list[str] = [
        ".mp4",
        ".mov",
        ".webm",
    ]
    SCHOOL_SCRAPER_AUDIO_EXTENSIONS: list[str] = [
        ".mp3",
        ".wav",
        ".m4a",
    ]
    SCHOOL_SCRAPER_DOCUMENT_EXTENSIONS: list[str] = [
        ".pdf",
        ".docx",
        ".doc",
        ".xlsx",
        ".xls",
        ".pptx",
        ".ppt",
    ]
    SCHOOL_SCRAPER_MAX_CANDIDATES: int = 10
    SCHOOL_SCRAPER_MAX_CRAWL_DEPTH: int = 2
    SCHOOL_SCRAPER_MAX_PAGES_PER_CRAWL: int = 20
    # How many top candidate pages to follow for sub-link discovery
    SCHOOL_SCRAPER_MAX_CANDIDATE_FOLLOW_PAGES: int = 3

    # School scraper pipeline (knowledge base) settings.
    # Master toggle for the biweekly Celery beat schedule. When False, the
    # `scrape-schools-biweekly` entry is skipped (manual triggers via the
    # API still work).
    SCHOOL_SCRAPER_CRON_ENABLED: bool = True
    # Fetch YouTube transcripts via yt-dlp (no video download) when True.
    # When False, youtube media items are recorded but skipped at ingest.
    SCHOOL_SCRAPER_YOUTUBE_TRANSCRIPT_ENABLED: bool = True
    # Transcribe audio/video via Whisper. When False, audio/video media
    # items are recorded and archived to S3 but no transcript is embedded.
    SCHOOL_SCRAPER_WHISPER_TRANSCRIPTION_ENABLED: bool = True
    # S3 path prefix for scraped media. Final key layout is:
    #   {SCHOOL_SCRAPER_S3_PREFIX}tenants/{tenant_id}/schools/{org_code}/...
    SCHOOL_SCRAPER_S3_PREFIX: str = ""
    # Concurrency for per-school scrape sub-tasks within a single cycle.
    SCHOOL_SCRAPER_CYCLE_CONCURRENCY: int = 5
    # Schema-driven crawler POC (experiment branch only). Model used by
    # scripts/school_data/schema_crawl_poc; defaults to the heatmap doc
    # classifier model when unset. Not used by SchoolScraperService.
    SCHOOL_SCRAPER_LLM_PAGE_CLASSIFIER_MODEL: str = "openai/gpt-4o-mini"

    # Email / SMTP
    SMTP_HOST: str | None = None
    SMTP_PORT: int | None = None
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    SMTP_FROM: str | None = None
    FRONTEND_BASE_URL: str = "http://localhost:3000"
    EMAIL_VERIFICATION_EXPIRE_HOURS: int = 24
    EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS: int = 60
    PASSWORD_RESET_EXPIRE_HOURS: int = 2
    INVITATION_EXPIRE_DAYS: int = 7
    INVITATION_RESEND_COOLDOWN_SECONDS: int = 60

    @validator("BACKEND_CORS_ORIGINS", pre=True)
    def assemble_cors_origins(cls, v):
        if isinstance(v, str) and not v.startswith("["):
            return [i.strip() for i in v.split(",")]
        elif isinstance(v, list | str):
            return v
        raise ValueError(v)

    @validator("CHROMA_PERSIST_DIR", pre=True, always=True)
    def make_chroma_persist_dir_absolute(cls, v):
        """Ensure CHROMA_PERSIST_DIR is an absolute path resolved from project root.

        Relative values should not depend on the current working directory at runtime.
        We resolve them relative to the repository root (two levels up from this file).
        """
        if not v:
            return v
        if os.path.isabs(v):
            return v
        project_root = Path(__file__).resolve().parents[2]
        return str((project_root / v).resolve())

    @validator("IMAGE_STORAGE_DIR", pre=True, always=True)
    def make_image_storage_dir_absolute(cls, v):
        """Ensure IMAGE_STORAGE_DIR is an absolute path resolved from project root.

        Relative values should not depend on the current working directory at runtime.
        We resolve them relative to the repository root (two levels up from this file).
        """
        if not v:
            return v
        if os.path.isabs(v):
            return v
        project_root = Path(__file__).resolve().parents[2]
        return str((project_root / v).resolve())

    @validator("TEMP_UPLOAD_DIR", pre=True, always=True)
    def make_temp_upload_dir_absolute(cls, v):
        """Ensure TEMP_UPLOAD_DIR is an absolute path resolved from project root.

        Relative values should not depend on the current working directory at runtime.
        We resolve them relative to the repository root (two levels up from this file).
        """
        if not v:
            return v
        if os.path.isabs(v):
            return v
        project_root = Path(__file__).resolve().parents[2]
        return str((project_root / v).resolve())

    @property
    def llm_api_key(self) -> str | None:
        """API key for the configured LLM provider."""
        if self.LLM_API_PROVIDER == "openrouter":
            return self.OPENROUTER_API_KEY or self.OPENAI_API_KEY
        return self.OPENAI_API_KEY

    @property
    def llm_api_base_url(self) -> str | None:
        """Base URL for the configured LLM provider (None = OpenAI default)."""
        if self.LLM_API_PROVIDER == "openrouter":
            return self.OPENROUTER_BASE_URL
        return None

    @property
    def DATABASE_URL(self) -> str:
        """Async-driver database URL (asyncpg). SSL handled via connect_args."""
        return (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def SYNC_DATABASE_URL(self) -> str:
        """Sync-driver database URL (psycopg2/psycopg3).
        Appends sslmode query param when POSTGRES_SSLMODE is set, which is the
        correct way to enable TLS for synchronous drivers (Alembic, langgraph
        AsyncPostgresSaver)."""
        base = (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )
        if self.POSTGRES_SSLMODE:
            return f"{base}?sslmode={self.POSTGRES_SSLMODE}"
        return base

    @property
    def REDIS_URL(self) -> str:
        """Construct Redis URL"""
        if self.REDIS_PASSWORD:
            return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"  # Ignore extra environment variables


settings = Settings()
