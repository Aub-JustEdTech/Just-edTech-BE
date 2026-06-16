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
    CHATBOT_DEFAULT_CHAT_MODEL: str = "gpt-4o-mini"
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

    # OpenAI API (API_KEY must be set in .env)
    OPENAI_API_KEY: str | None = None
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-ada-002"

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
    def DATABASE_URL(self) -> str:
        """Construct database URL"""
        return (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

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