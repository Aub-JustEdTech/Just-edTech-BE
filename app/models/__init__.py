"""
SQLAlchemy models for Just-EdTech application.
"""

from app.models.api_keys import ApiKey
from app.models.associations import conversation_documents
from app.models.base import Base
from app.models.billing import Billing
from app.models.chat_consumers import ChatConsumer
from app.models.citations import Citation
from app.models.conversations import Conversation, Message
from app.models.daily_token_usage import DailyTokenUsage
from app.models.documents import Document, ProcessingStatus
from app.models.feedback import Feedback
from app.models.invitations import Invitation
from app.models.llm_models import LLMModel
from app.models.monitoring import Monitoring
from app.models.monthly_billing import MonthlyBilling
from app.models.processing_jobs import DocumentProcessingJob, JobStatus
from app.models.processing_stages import (
    DocumentProcessingStage,
    ProcessingStage,
    StageStatus,
)
from app.models.roles import Role
from app.models.signups import Signup
from app.models.chatbot_configs import ChatbotConfig, PerformanceMetric
from app.models.image_captions import ImageCaption
from app.models.tenants import Tenant
from app.models.upload_batches import BatchStatus, UploadBatch
from app.models.users import User

__all__ = [
    "Base",
    "Tenant",
    "User",
    "ApiKey",
    "Conversation",
    "Message",
    "Citation",
    "Feedback",
    "Invitation",
    "Document",
    "ProcessingStatus",
    "DocumentProcessingJob",
    "JobStatus",
    "DocumentProcessingStage",
    "ProcessingStage",
    "StageStatus",
    "conversation_documents",
    "ChatbotConfig",
    "PerformanceMetric",
    "LLMModel",
    "Monitoring",
    "Billing",
    "Role",
    "UploadBatch",
    "BatchStatus",
    "Signup",
    "ChatConsumer",
    "DailyTokenUsage",
    "MonthlyBilling",
    "ImageCaption",
]
