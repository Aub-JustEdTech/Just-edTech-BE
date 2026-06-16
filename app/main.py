"""
Just-EdTech FastAPI Application

A modern, scalable FastAPI-based backend for Retrieval-Augmented Generation (RAG)
with modular architecture and robust authentication.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

# Initialize LangSmith tracing early
from app.services.observability import initialize_langsmith

from app.api.api import api_router
from app.core.config import settings
from app.db.connector import create_tables
from app.db.redis_connector import close_redis, init_redis
from app.utils.exception_handlers import (
    general_exception_handler,
    http_exception_handler,
    validation_exception_handler,
)
from app.utils.response import success_response


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""
    initialize_langsmith()
    await create_tables()
    await init_redis()
    yield
    await close_redis()


app = FastAPI(
    title="Just-EdTech API",
    description="A modern, scalable FastAPI-based backend for Retrieval-Augmented Generation (RAG)",
    version="1.0.0",
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# Normalize AnyHttpUrl -> plain strings without trailing slashes for strict Origin match
_origins = [str(o).rstrip("/") for o in settings.BACKEND_CORS_ORIGINS]

# Set up CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add exception handlers for standardized error responses
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, general_exception_handler)

# Include API router
app.include_router(api_router, prefix=settings.API_V1_STR)


@app.get("/")
async def root():
    """Health check endpoint"""

    return success_response(
        data={
            "message": "Just-EdTech API is running",
            "version": "1.0.0",
            "docs": "/docs",
        }
    )


@app.get("/health")
async def health_check():
    """Health check endpoint for monitoring"""
    return success_response(data={"status": "healthy", "service": "just-edtech"})
