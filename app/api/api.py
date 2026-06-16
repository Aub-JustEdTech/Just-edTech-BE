"""
Main API router for Just-EdTech application.
"""

from fastapi import APIRouter

from app.api.endpoints import (
    admin,
    analytics,
    api_keys,
    auth,
    chat_auth,
    chatbots,
    conversations,
    daily_token_usage,
    documents,
    invitations,
    llm_models,
    monthly_billing,
    pipeline_status,
    rag,
    upload_batches,
)

api_router = APIRouter()

# Include all endpoint routers
api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
api_router.include_router(
    chat_auth.router, prefix="/chat-auth", tags=["Chat Consumer Authentication"]
)
api_router.include_router(admin.router, prefix="/admin", tags=["Administration"])
api_router.include_router(api_keys.router, prefix="/api-keys", tags=["API Keys"])
api_router.include_router(documents.router, prefix="/documents", tags=["Documents"])
api_router.include_router(
    conversations.router, prefix="/conversations", tags=["Conversations"]
)
api_router.include_router(chatbots.router, prefix="/chatbots", tags=["Chatbots"])
api_router.include_router(rag.router, prefix="/rag", tags=["RAG"])
api_router.include_router(
    upload_batches.router, prefix="/batches", tags=["Upload Batches"]
)
api_router.include_router(
    invitations.router, prefix="/invitations", tags=["Invitations"]
)
api_router.include_router(pipeline_status.router, tags=["Pipeline Status"])
api_router.include_router(analytics.router, prefix="/analytics", tags=["Analytics"])
api_router.include_router(
    daily_token_usage.router, prefix="/token-usage", tags=["Token Usage"]
)
api_router.include_router(
    monthly_billing.router, prefix="/billing", tags=["Monthly Billing"]
)
api_router.include_router(
    llm_models.router, prefix="/llm-models", tags=["LLM Models"]
)
