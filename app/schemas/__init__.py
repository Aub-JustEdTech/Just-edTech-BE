"""
Pydantic schemas for request/response validation.
"""

from app.schemas.chat_consumers import (
    ChatConsumerCreate,
    ChatConsumerRegisterRequest,
    ChatConsumerRegisterResponse,
    ChatConsumerResponse,
)
from app.schemas.citations import CitationCreate, CitationResponse
from app.schemas.common import APIResponse, ErrorDetail
from app.schemas.conversations import (
    ConversationCreate,
    ConversationListResponse,
    ConversationResponse,
    ConversationUpdate,
    PaginationResponse,
)
from app.schemas.documents import Document, DocumentCreate, DocumentInDB, DocumentUpdate
from app.schemas.messages import (
    MessageCreate,
    MessageListResponse,
    MessageResponse,
    SendMessageRequest,
    SendMessageResponse,
)
from app.schemas.rag import RAGQuery, RAGResponse
from app.schemas.users import User, UserCreate, UserInDB, UserUpdate
from app.schemas.chatbot_configs import (
    ChatbotConfigCreate,
    ChatbotConfigListResponse,
    ChatbotConfigResponse,
    ChatbotConfigUpdate,
)

__all__ = [
    "User",
    "UserCreate",
    "UserInDB",
    "UserUpdate",
    "Document",
    "DocumentCreate",
    "DocumentInDB",
    "DocumentUpdate",
    "ConversationCreate",
    "ConversationResponse",
    "ConversationUpdate",
    "ConversationListResponse",
    "PaginationResponse",
    "MessageCreate",
    "MessageResponse",
    "MessageListResponse",
    "SendMessageRequest",
    "SendMessageResponse",
    "CitationCreate",
    "CitationResponse",
    "RAGQuery",
    "RAGResponse",
    "ChatConsumerCreate",
    "ChatConsumerResponse",
    "ChatConsumerRegisterRequest",
    "ChatConsumerRegisterResponse",
    "ChatbotConfigCreate",
    "ChatbotConfigUpdate",
    "ChatbotConfigResponse",
    "ChatbotConfigListResponse",
    "APIResponse",
    "ErrorDetail",
]
