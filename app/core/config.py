"""
Core configuration settings for Just-EdTech application.
"""

import os
from datetime import date
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

    # Separate Celery broker/backend Redis. When unset, falls back to the
    # app-cache Redis (REDIS_HOST:REDIS_PORT / db 2) — preserving the old
    # single-Redis behavior for local dev. In prod (docker-compose.prod.yml)
    # this points at a dedicated redis-broker container so a cache-heavy burst
    # cannot evict in-flight Celery messages (the root cause of the
    # allkeys-lru silent-chain-break incident) and the broker's noeviction
    # policy doesn't make app-cache writes fail loudly.
    CELERY_BROKER_REDIS_HOST: str | None = None
    CELERY_BROKER_REDIS_PORT: int | None = None
    CELERY_BROKER_REDIS_DB: int = 2
    CELERY_BROKER_REDIS_PASSWORD: str | None = None
    # Result backend (often same as broker). Separate override only if needed.
    CELERY_BACKEND_REDIS_HOST: str | None = None
    CELERY_BACKEND_REDIS_PORT: int | None = None
    CELERY_BACKEND_REDIS_DB: int | None = None
    CELERY_BACKEND_REDIS_PASSWORD: str | None = None

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
    CHATBOT_DEFAULT_SYSTEM_PROMPT: str = "You are a helpful assistant that answers using the organization's knowledge base."
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
    CHATBOT_DEFAULT_IMAGE_CONTEXT_CHARS: int = (
        200  # Characters before/after image for context
    )

    # Vector Database Configuration
    VECTOR_STORE_TYPE: str = "qdrant"  # chroma, qdrant, pinecone, weaviate, etc.
    CHROMA_PERSIST_DIR: str = "./chroma_db"
    CHROMA_COLLECTION_PREFIX: str = "tenant"
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_COLLECTION_PREFIX: str = "justedtech"
    # Points per upsert call. A single request carrying hundreds of points
    # (each with a full-text payload) can exceed the client's write timeout.
    QDRANT_UPSERT_BATCH_SIZE: int = 100
    # qdrant-client's own default (5s) is too short once the collection is
    # under sustained load (e.g. apply_batch_results writing thousands of
    # points back-to-back) -- observed causing widespread ReadTimeouts on
    # an 8k-chunk apply run.
    QDRANT_CLIENT_TIMEOUT_SECONDS: int = 30
    # Per-point set_payload retries during apply_batch_results before a
    # chunk is given up on and marked 'failed'. A raised timeout budget
    # (above) covers most cases; this covers the rest without needing a
    # full batch resubmission for a handful of transient blips.
    HEATMAP_INGEST_APPLY_SET_PAYLOAD_RETRIES: int = 2

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
    # Document summarizer (app/services/document_processing/summarizer.py).
    DOCUMENT_SUMMARIZER_MODEL: str = "openai/gpt-4o-mini"
    # Max chunks per OpenAI Batch API submission. The API caps at 50,000
    # requests per batch; we use a smaller default to keep batches quick.
    HEATMAP_INGEST_BATCH_SIZE: int = 50_000
    # Max bytes for one OpenAI Batch input JSONL. The API hard-caps a batch
    # input file at 200 MB; with a ~24 KB system prompt each line is ~35 KB,
    # so the file-size cap binds well before the 50,000-request cap
    # (50k * 35 KB ~ 1.7 GB). Default 180 MB yields ~5,000 lines per batch,
    # so a ~116k-chunk corpus needs ~23-30 batches.
    HEATMAP_INGEST_BATCH_MAX_BYTES: int = 180 * 1024 * 1024
    # A chunk whose batch ends failed/expired/cancelled is reset to
    # 'pending' for resubmission. After this many resets it's parked at
    # 'dead_letter' instead, so a batch that keeps failing for a content
    # reason (not a transient bug) doesn't retry forever.
    HEATMAP_INGEST_MAX_BATCH_RETRIES: int = 3
    # apply_batch_results commits progress every N processed chunks instead
    # of once at the very end. Without this, a single failure late in a
    # large batch (e.g. one bad heatmap_aggregate row) rolls back every
    # chunk's classification result, not just the one that failed.
    HEATMAP_INGEST_APPLY_COMMIT_BATCH_SIZE: int = 200
    # When True, the heatmap service returns canned sample data instead of
    # reading heatmap_aggregate + Qdrant. Useful for local dev without a
    # populated vector store. Default False (use real data).
    HEATMAP_USE_SAMPLE_DATA: bool = False
    # Per-chunk contextual augmentation (Anthropic-style "contextual
    # retrieval"). One LLM call per chunk, full source doc as reference,
    # generates a short situating context prepended before embedding.
    # Disabled by default; flip to True to enable the step2_7 stage for
    # school_scraper docs.
    HEATMAP_CONTEXT_ENABLED: bool = True
    HEATMAP_CONTEXT_MODEL: str = "openai/gpt-4o-mini"
    HEATMAP_CONTEXT_MAX_CONCURRENCY: int = 5

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
    # Media accepted for storage. These are transcribed, not text-extracted, so
    # they are kept separate from ALLOWED_DOCUMENT_TYPES — there is no document
    # processor for them. Extensions are matched lowercased, so ".WAV" is fine.
    ALLOWED_MEDIA_TYPES: list[str] = [
        ".mp3",
        ".mp4",
        ".wav",
        ".m4a",
        ".webm",
        ".mov",
    ]
    # Media gets its own ceiling. MAX_FILE_SIZE_MB is sized for documents and
    # an hour of video is an order of magnitude past it. Uploads stream
    # straight to S3, so this bounds storage and spend, not memory.
    MAX_MEDIA_FILE_SIZE_MB: int = 500
    TEMP_UPLOAD_DIR: str = "./temp_uploads"
    IMAGE_STORAGE_DIR: str = "./data/images"
    ENABLE_IMAGE_EXTRACTION: bool = False

    # OCR fallback for scanned / image-only PDFs (Phase 1: Tesseract).
    # When digital text extraction yields fewer than OCR_MIN_CHARS_THRESHOLD
    # characters, empty/sparse pages are rendered and OCR'd.
    ENABLE_OCR: bool = False
    OCR_PROVIDER: str = "tesseract"
    OCR_MIN_CHARS_THRESHOLD: int = 50
    OCR_DPI: int = 150
    OCR_MAX_PAGES: int = 100
    OCR_LANGUAGES: str = "eng"
    OCR_TIMEOUT_SECONDS: int = 300

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
    # Raised from (5 / 50000 / 120) to (12 / 120000 / 240) so the
    # cross-district analytics archetype (taxonomy → count_districts_by_topic
    # → get_district_citations per top-N → synthesise) has room to complete.
    # Each count_districts_by_topic call is one tool round; each
    # get_district_citations call is another; ~10 drill-downs + synthesis
    # comfortably fits in 12 iterations and 120k tokens.
    AGENT_MAX_ITERATIONS: int = 12
    AGENT_MAX_TOKENS_BUDGET: int = 120000
    AGENT_TIMEOUT_SECONDS: int = 240

    # Web Scraping Configuration
    WEB_SCRAPER_TIMEOUT_SECONDS: int = (
        30  # Default timeout in seconds for web scraping requests
    )

    # School Scraper Configuration
    SCHOOL_SCRAPER_USE_PLAYWRIGHT: bool = False
    # User-Agent used by the school scraper for HTTP + Playwright requests.
    # Prefer a browser-like UA: eSchoolView / Blackboard / Drive embeds often
    # reject curl-style UAs. Override via env to ``curl/8.5.0`` if a site's
    # Wordfence/Cloudflare WAF blocks Chrome UAs instead.
    SCHOOL_SCRAPER_USER_AGENT: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
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
        ".txt",
        ".text",
        ".xlsx",
        ".xls",
        ".pptx",
        ".ppt",
    ]
    SCHOOL_SCRAPER_MAX_CANDIDATES: int = 10
    SCHOOL_SCRAPER_MAX_CRAWL_DEPTH: int = 4
    SCHOOL_SCRAPER_MAX_PAGES_PER_CRAWL: int = 20
    # How many top candidate pages to follow for sub-link discovery
    SCHOOL_SCRAPER_MAX_CANDIDATE_FOLLOW_PAGES: int = 3

    SCHOOL_SCRAPER_YOUTUBE_TRANSCRIPT_ENABLED: bool = True
    SCHOOL_SCRAPER_AV_ONLY_MODE: bool = False
    SCHOOL_SCRAPER_WHISPER_TRANSCRIPTION_ENABLED: bool = True
    SCHOOL_SCRAPER_S3_PREFIX: str = ""
    SCHOOL_SCRAPER_ALLOWED_YEARS: list[int] = [2023, 2024, 2025, 2026]
    SCHOOL_SCRAPER_DOWNLOAD_ON_UNKNOWN_YEAR: bool = False
    SCHOOL_SCRAPER_MEDIA_CUTOFF_DATE: date | None = date(2026, 9, 1)
    SCHOOL_SCRAPER_LLM_PAGE_CLASSIFIER_MODEL: str = "openai/gpt-4o-mini"
    SCHOOL_SCRAPER_RANKING_MODE: str = "both"
    SCHOOL_SCRAPER_LLM_MAX_PAGES: int = 15
    SCHOOL_SCRAPER_LLM_MAX_DEPTH: int = 4
    SCHOOL_SCRAPER_LLM_CONFIDENCE_THRESHOLD: float = 0.5
    SCHOOL_SCRAPER_LLM_SKIP_ARCHIVAL: bool = False
    SCHOOL_SCRAPER_BOARD_PLATFORM_DOMAINS: list[str] = [
        "boarddocs.com",
        "diligentoneplatform.com",
        "boardontrack.com",
        "granicus.com",
    ]
    SCHOOL_SCRAPER_BOARD_PORTAL_MAX_MEETINGS: int = 24
    # Max schools crawled per sweep_school_media run. A sweep enqueues one
    # ingest task per newly discovered media item, and each ingest spawns a
    # 9-stage document pipeline chain in Redis (noeviction broker). At 3-4
    # docs/district, 50 schools => ~150-200 ingest tasks/run, which keeps the
    # broker bounded while workers drain it. 0 = unlimited (full corpus).
    # Round-robin ordering (last_scrapped_at ASC NULLS FIRST) means later
    # runs cover different schools instead of re-crawling the same head.
    SCHOOL_SCRAPER_SWEEP_MAX_SCHOOLS: int = 50
    # Max newly-discovered media items to ENQUEUE per URL per sweep. The rest
    # are still persisted as scraped_media (status="discovered") but stay
    # unqueued -- they are picked up by the drain-discovered task on later
    # runs. This is what stops a first-time depth-2 crawl of a meeting archive
    # (which can find 100+ historical docs) from enqueuing all of them at once.
    # 0 = unlimited (enqueue all newly created rows, original behavior).
    SCHOOL_SCRAPER_SWEEP_MAX_ENQUEUE_PER_URL: int = 5
    # Offline URL-discovery candidates JSON (used by scrape-url-candidates API).
    SCHOOL_URL_CANDIDATES_JSON_PATH: str = (
        "scripts/school_data/output/selected_schools_url_candidates_both.json"
    )

    # --- Transcription: AssemblyAI ---
    ASSEMBLYAI_API_KEY: str = ""
    ASSEMBLYAI_BASE_URL: str = "https://api.assemblyai.com"
    ASSEMBLYAI_SPEECH_MODELS: list[str] = ["universal-3-5-pro", "universal-2"]
    ASSEMBLYAI_SPEAKER_LABELS: bool = True
    ASSEMBLYAI_LANGUAGE_CODE: str = "en"
    ASSEMBLYAI_POLL_INTERVAL_SECONDS: int = 15
    ASSEMBLYAI_POLL_TIMEOUT_SECONDS: int = 2400

    # --- Transcription: audio handling ---
    TRANSCRIPTION_AUDIO_MODE: str = "url_direct"
    TRANSCRIPTION_FFMPEG_PATH: str = "ffmpeg"
    TRANSCRIPTION_FFPROBE_PATH: str = "ffprobe"
    TRANSCRIPTION_FFPROBE_TIMEOUT_SECONDS: int = 30
    TRANSCRIPTION_SAMPLE_RATE_HZ: int = 16000
    TRANSCRIPTION_HIGHPASS_HZ: int = 80
    TRANSCRIPTION_DENOISE_ENABLED: bool = True
    TRANSCRIPTION_GAIN_DB: float = 0.0
    TRANSCRIPTION_FFMPEG_TIMEOUT_SECONDS: int = 1800
    TRANSCRIPTION_CHUNK_TARGET_SECONDS: int = 90
    TRANSCRIPTION_CHUNK_MAX_CHARS: int = 4000

    # --- Transcription: caps / cost control ---
    SCHOOL_SCRAPER_MEDIA_MAX_DOWNLOAD_MB: int = 1024
    SCHOOL_SCRAPER_MEDIA_MAX_DURATION_MINUTES: int = 300
    SCHOOL_SCRAPER_MEDIA_TEMP_DIR: str = "./temp_uploads/media"
    SCHOOL_SCRAPER_MEDIA_REQUIRE_AUDIO: bool = True
    SCHOOL_SCRAPER_MEDIA_MIN_DURATION_SECONDS: int = 0

    # --- Transcription: YouTube ---
    SCHOOL_SCRAPER_YOUTUBE_AUDIO_FALLBACK_ENABLED: bool = True
    SCHOOL_SCRAPER_YOUTUBE_SUBTITLE_LANGS: list[str] = ["en", "en-US", "en-GB"]
    SCHOOL_SCRAPER_YOUTUBE_PROXY_URL: str = ""
    SCHOOL_SCRAPER_YOUTUBE_CAPTION_BUDGET: int = 10
    YOUTUBE_DATA_API_KEY: str = ""
    YOUTUBE_DATA_API_BASE_URL: str = "https://www.googleapis.com/youtube/v3"

    # --- Transcription: Supadata (YouTube fallback tier 2) ---
    SUPADATA_API_KEY: str = ""
    SUPADATA_BASE_URL: str = "https://api.supadata.ai/v1"
    SCHOOL_SCRAPER_SUPADATA_ENABLED: bool = True

    # --- Transcription: neutral gate names ---
    TRANSCRIPTION_ENABLED: bool | None = None
    TRANSCRIPTION_MAX_DURATION_MINUTES: int | None = None
    TRANSCRIPTION_MIN_DURATION_SECONDS: int | None = None
    TRANSCRIPTION_REQUIRE_AUDIO: bool | None = None
    TRANSCRIPTION_YOUTUBE_ENABLED: bool | None = None
    TRANSCRIPTION_YOUTUBE_AUDIO_FALLBACK_ENABLED: bool | None = None

    # --- Media ingest: tenant uploads and pasted links ---
    MEDIA_INGEST_TEMP_DIR: str = "./temp_uploads/media_ingest"
    MEDIA_INGEST_PRESIGN_EXPIRY_SECONDS: int = 7200
    TENANT_MEDIA_MONTHLY_MINUTES_LIMIT: int = 600
    TRANSCRIPTION_COST_PER_AUDIO_HOUR_USD: float = 0.23

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

    @validator("SCHOOL_URL_CANDIDATES_JSON_PATH", pre=True, always=True)
    def make_school_url_candidates_json_path_absolute(cls, v):
        if not v:
            return v
        if os.path.isabs(v):
            return v
        project_root = Path(__file__).resolve().parents[2]
        return str((project_root / v).resolve())

    @validator("TRANSCRIPTION_AUDIO_MODE", pre=True, always=True)
    def validate_transcription_audio_mode(cls, v):
        """Reject unknown modes at startup rather than at first transcription."""
        allowed = {"url_direct", "preprocess"}
        if v not in allowed:
            raise ValueError(
                f"TRANSCRIPTION_AUDIO_MODE must be one of {sorted(allowed)}, got {v!r}"
            )
        return v

    @validator("SCHOOL_SCRAPER_MEDIA_TEMP_DIR", pre=True, always=True)
    def make_media_temp_dir_absolute(cls, v):
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

    # --- Transcription gate resolution ---
    # Every caller reads these, never the raw settings, so the scraper and
    # tenant uploads can never end up on different values by accident.

    @property
    def transcription_enabled(self) -> bool:
        if self.TRANSCRIPTION_ENABLED is not None:
            return self.TRANSCRIPTION_ENABLED
        return self.SCHOOL_SCRAPER_WHISPER_TRANSCRIPTION_ENABLED

    @property
    def transcription_max_duration_minutes(self) -> int:
        if self.TRANSCRIPTION_MAX_DURATION_MINUTES is not None:
            return self.TRANSCRIPTION_MAX_DURATION_MINUTES
        return self.SCHOOL_SCRAPER_MEDIA_MAX_DURATION_MINUTES

    @property
    def transcription_min_duration_seconds(self) -> int:
        if self.TRANSCRIPTION_MIN_DURATION_SECONDS is not None:
            return self.TRANSCRIPTION_MIN_DURATION_SECONDS
        return self.SCHOOL_SCRAPER_MEDIA_MIN_DURATION_SECONDS

    @property
    def transcription_require_audio(self) -> bool:
        if self.TRANSCRIPTION_REQUIRE_AUDIO is not None:
            return self.TRANSCRIPTION_REQUIRE_AUDIO
        return self.SCHOOL_SCRAPER_MEDIA_REQUIRE_AUDIO

    @property
    def transcription_youtube_enabled(self) -> bool:
        if self.TRANSCRIPTION_YOUTUBE_ENABLED is not None:
            return self.TRANSCRIPTION_YOUTUBE_ENABLED
        return self.SCHOOL_SCRAPER_YOUTUBE_TRANSCRIPT_ENABLED

    @property
    def transcription_youtube_audio_fallback_enabled(self) -> bool:
        if self.TRANSCRIPTION_YOUTUBE_AUDIO_FALLBACK_ENABLED is not None:
            return self.TRANSCRIPTION_YOUTUBE_AUDIO_FALLBACK_ENABLED
        return self.SCHOOL_SCRAPER_YOUTUBE_AUDIO_FALLBACK_ENABLED

    @property
    def REDIS_URL(self) -> str:
        """Construct Redis URL for app cache (tokens, OTP, invites)."""
        if self.REDIS_PASSWORD:
            return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    @property
    def CELERY_BROKER_URL(self) -> str:
        """Celery broker URL. Falls back to app-cache Redis db/2 when unset."""
        host = self.CELERY_BROKER_REDIS_HOST or self.REDIS_HOST
        port = self.CELERY_BROKER_REDIS_PORT or self.REDIS_PORT
        password = self.CELERY_BROKER_REDIS_PASSWORD or self.REDIS_PASSWORD
        db = self.CELERY_BROKER_REDIS_DB
        if password:
            return f"redis://:{password}@{host}:{port}/{db}"
        return f"redis://{host}:{port}/{db}"

    @property
    def CELERY_BACKEND_URL(self) -> str:
        """Celery result-backend URL. Defaults to the broker URL."""
        host = self.CELERY_BACKEND_REDIS_HOST or self.CELERY_BROKER_REDIS_HOST or self.REDIS_HOST
        port = self.CELERY_BACKEND_REDIS_PORT or self.CELERY_BROKER_REDIS_PORT or self.REDIS_PORT
        password = (
            self.CELERY_BACKEND_REDIS_PASSWORD
            or self.CELERY_BROKER_REDIS_PASSWORD
            or self.REDIS_PASSWORD
        )
        db = (
            self.CELERY_BACKEND_REDIS_DB
            if self.CELERY_BACKEND_REDIS_DB is not None
            else self.CELERY_BROKER_REDIS_DB
        )
        if password:
            return f"redis://:{password}@{host}:{port}/{db}"
        return f"redis://{host}:{port}/{db}"

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"  # Ignore extra environment variables


settings = Settings()
