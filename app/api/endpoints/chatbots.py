"""
Chatbot configuration management endpoints.
"""

import json
import logging
import math
import os
from typing import Any
import mimetypes

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.crud.chatbot_configs import chatbot_config
from app.schemas.chatbot_configs import (
    ChatbotConfigCreate,
    ChatbotConfigDefaultsResponse,
    ChatbotConfigListResponse,
    ChatbotConfigResponse,
    ChatbotConfigUpdate,
)
from app.schemas.users import User
from app.models.chat_consumers import ChatConsumer
from app.models.tenants import Tenant
from app.utils.avatar_upload import delete_avatar, upload_avatar
from app.utils.dependencies import get_current_user, get_db, require_user_or_chat_consumer
from app.utils.response import success_response
from app.utils.s3 import S3Manager, extract_s3_key_from_url

router = APIRouter()
logger = logging.getLogger(__name__)

def _limit_history_to_latest(response: ChatbotConfigResponse) -> ChatbotConfigResponse:
    """Keep only the latest config version in the response."""
    if response.config_version_history:
        response.config_version_history = response.config_version_history[:1]
    return response


def _build_chatbot_defaults() -> ChatbotConfigDefaultsResponse:
    """Provide default values for chatbot creation forms."""
    base_defaults = {
        "name": settings.CHATBOT_DEFAULT_NAME,
        "title": settings.CHATBOT_DEFAULT_TITLE,
        "welcome_message": settings.CHATBOT_DEFAULT_WELCOME_MESSAGE,
        "bot_avatar": None,
        "is_default": False,
    }

    config_defaults: dict[str, Any] = {
        "system_prompt": settings.CHATBOT_DEFAULT_SYSTEM_PROMPT,
        "chat_model_id": None,
        "chat_model": settings.CHATBOT_DEFAULT_CHAT_MODEL,
        "chat_temperature": settings.CHATBOT_DEFAULT_CHAT_TEMPERATURE,
        "chat_max_tokens": settings.MAX_TOKENS,
        "embedding_model_id": None,
        "embedding_model": settings.OPENAI_EMBEDDING_MODEL,
        "vec_db_id": None,
        "vector_store_type": settings.VECTOR_STORE_TYPE,
        "search_type": settings.CHATBOT_DEFAULT_SEARCH_TYPE,
        "threshold_value": settings.CHATBOT_DEFAULT_THRESHOLD_VALUE,
        "rag_top_k": settings.CHATBOT_DEFAULT_RAG_TOP_K,
        "rag_max_history": settings.CHATBOT_DEFAULT_RAG_MAX_HISTORY,
        "rag_context_chars": settings.CHATBOT_DEFAULT_RAG_CONTEXT_CHARS,
        "rag_snippet_chars": settings.CHATBOT_DEFAULT_RAG_SNIPPET_CHARS,
        "chunk_size": settings.CHUNK_SIZE,
        "chunk_overlap": settings.CHUNK_OVERLAP,
        "openai_timeout_s": settings.CHATBOT_DEFAULT_OPENAI_TIMEOUT_S,
        "brand_color": settings.CHATBOT_DEFAULT_BRAND_COLOR,
        "personality": settings.CHATBOT_DEFAULT_PERSONALITY,
        "contact_link": settings.CHATBOT_DEFAULT_CONTACT_LINK,
        "similarity_score": settings.CHATBOT_DEFAULT_SIMILARITY_SCORE,
        "input_placeholder": settings.CHATBOT_DEFAULT_INPUT_PLACEHOLDER,
        "enable_prompt_suggestions": settings.CHATBOT_ENABLE_PROMPT_SUGGESTIONS,
        "enable_multimodal": settings.CHATBOT_DEFAULT_ENABLE_MULTIMODAL,
        "max_images": settings.CHATBOT_DEFAULT_MAX_IMAGES,
        "image_context_chars": settings.CHATBOT_DEFAULT_IMAGE_CONTEXT_CHARS,
    }

    return ChatbotConfigDefaultsResponse(
        base_defaults=base_defaults,
        config_defaults=config_defaults,
    )


async def get_avatar_presigned_url(bot_avatar: str | None) -> str | None:
    """
    Generate a presigned URL for bot avatar if it exists.
    
    Args:
        bot_avatar: S3 URL or key stored in database
        
    Returns:
        Presigned URL or None if bot_avatar is empty
    """
    if not bot_avatar:
        return None
    
    try:
        # Extract S3 key from stored URL (handles s3://, https://, or just key)
        s3_key = extract_s3_key_from_url(bot_avatar, settings.S3_BUCKET_NAME)
        if not s3_key:
            logger.warning(
                f"Could not extract S3 key from avatar URL: {bot_avatar}, "
                f"bucket: {settings.S3_BUCKET_NAME}"
            )
            return None
        
        logger.debug(f"Extracted S3 key '{s3_key}' from avatar URL: {bot_avatar}")
        
        # Derive content type from file extension (uploads already restrict types)
        filename = s3_key.rsplit("/", 1)[-1]
        content_type, _ = mimetypes.guess_type(filename)

        # Generate presigned URL (valid for 7 days for avatars)
        s3_manager = S3Manager(
            bucket_name=settings.S3_BUCKET_NAME,
            region_name=settings.S3_REGION,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        )
        
        presigned_url = await s3_manager.get_presigned_url(
            s3_key=s3_key,
            expiration=604800,  # 7 days
            http_method="GET",
            response_content_type=content_type,
        )
        
        logger.debug(f"Generated presigned URL for avatar: {presigned_url[:50]}...")
        return presigned_url
    except Exception as e:
        logger.error(
            f"Failed to generate presigned URL for avatar {bot_avatar}: {e}",
            exc_info=True
        )
        return None


@router.post(
    "",
    response_model=ChatbotConfigResponse,
    status_code=status.HTTP_201_CREATED,
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": ChatbotConfigCreate.model_json_schema(),
                    "example": {
                        "name": "My Bot",
                        "tenant_id": 1,
                        "title": "Customer Support",
                        "embedding_model_id": 1,
                        "chunk_size": 1000
                    }
                },
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["chatbot_config_body"],
                        "properties": {
                            "chatbot_config_body": {
                                "type": "string",
                                "description": "Chatbot configuration as JSON string",
                                "example": '{"name": "My Bot", "tenant_id": 1, "title": "Customer Support"}'
                            },
                            "avatar": {
                                "type": "string",
                                "format": "binary",
                                "description": "Optional avatar image file (jpg, jpeg, png, gif, webp, max 5MB)"
                            }
                        }
                    }
                }
            }
        }
    }
)
async def create_chatbot(
    request: Request,
    avatar: UploadFile | None = File(None, description="Optional avatar image file (jpg, jpeg, png, gif, webp, max 5MB)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Create a new chatbot configuration.
    
    Supports both JSON body and optional avatar file upload.
    When avatar is provided, use multipart/form-data format.
    
    **Example with avatar (multipart/form-data):**
    ```bash
    curl -X POST "http://localhost:8000/api/v1/chatbots" \
      -H "Authorization: Bearer YOUR_TOKEN" \
      -F 'chatbot_config_body={"name": "My Bot", "tenant_id": 1, ...}' \
      -F "avatar=@avatar.png"
    ```
    
    **Example without avatar (application/json):**
    ```bash
    curl -X POST "http://localhost:8000/api/v1/chatbots" \
      -H "Authorization: Bearer YOUR_TOKEN" \
      -H "Content-Type: application/json" \
      -d '{
        "name": "My Bot",
        "tenant_id": 1,
        "title": "Customer Support",
        "embedding_model_id": 1,
        "chunk_size": 1000
      }'
    ```
    """
    content_type = request.headers.get("content-type", "").lower()
    
    # Parse request based on content type
    if "multipart/form-data" in content_type:
        # Handle multipart/form-data
        form = await request.form()
        chatbot_config_json = form.get("chatbot_config_body")
        
        if not chatbot_config_json:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="chatbot_config_body form field is required for multipart/form-data requests",
            )
        
        # Parse JSON string
        try:
            if isinstance(chatbot_config_json, str):
                config_dict = json.loads(chatbot_config_json)
            else:
                config_dict = json.loads(str(chatbot_config_json))
            chatbot_config_create = ChatbotConfigCreate(**config_dict)
        except (json.JSONDecodeError, ValueError) as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid JSON in chatbot_config_body: {str(e)}",
            )
        
        # Get avatar from form if not already provided
        if not avatar:
            avatar_file = form.get("avatar")
            if avatar_file and isinstance(avatar_file, UploadFile):
                avatar = avatar_file
    else:
        # Handle application/json
        try:
            body = await request.json()
            chatbot_config_create = ChatbotConfigCreate(**body)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid request body: {str(e)}",
            )
    
    # Handle avatar upload if provided
    avatar_url = chatbot_config_create.bot_avatar
    if avatar:
        try:
            # Validate file extension
            file_extension = os.path.splitext(avatar.filename)[1].lower()
            if file_extension not in settings.ALLOWED_AVATAR_TYPES:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Avatar file type {file_extension} not allowed. Supported types: {', '.join(settings.ALLOWED_AVATAR_TYPES)}",
                )

            # Read file content
            file_content = await avatar.read()
            file_size = len(file_content)

            # Validate file size
            max_size = settings.MAX_AVATAR_SIZE_MB * 1024 * 1024
            if file_size > max_size:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"Avatar file size exceeds {settings.MAX_AVATAR_SIZE_MB}MB limit",
                )

            # Upload avatar to S3
            avatar_url = await upload_avatar(
                file_content=file_content,
                file_name=avatar.filename,
                tenant_id=chatbot_config_create.tenant_id,
                file_size=file_size,
            )
            
            # Set the avatar URL in the config
            chatbot_config_create.bot_avatar = avatar_url
        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            )
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to upload avatar: {str(e)}",
            )
    
    # Verify tenant ownership
    if chatbot_config_create.tenant_id != current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot create chatbot for different tenant",
        )

    # Check if name already exists for this tenant
    existing_chatbots = await chatbot_config.list_by_tenant(
        db, chatbot_config_create.tenant_id
    )
    for bot in existing_chatbots:
        if bot.name == chatbot_config_create.name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Chatbot with name '{chatbot_config_create.name}' already exists for this tenant",
            )

    created_chatbot = await chatbot_config.create(db, chatbot_config_create)
    response_data = _limit_history_to_latest(
        ChatbotConfigResponse.from_orm(created_chatbot)
    )
    if created_chatbot.bot_avatar:
        response_data.bot_avatar = await get_avatar_presigned_url(created_chatbot.bot_avatar)
    return success_response(
        data=response_data,
        status_code=status.HTTP_201_CREATED,
    )


@router.get("", response_model=ChatbotConfigListResponse)
async def list_chatbots(
    tenant_id: int | None = Query(None, description="Filter by tenant ID"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List chatbot configurations"""
    # Use current user's tenant if not specified
    filter_tenant_id = tenant_id or current_user.tenant_id

    # Verify tenant access
    if filter_tenant_id != current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot access chatbots from different tenant",
        )

    chatbots = await chatbot_config.list_by_tenant(db, filter_tenant_id)
    total = len(chatbots)

    # Apply pagination
    offset = (page - 1) * per_page
    paginated_chatbots = chatbots[offset : offset + per_page]
    pages = math.ceil(total / per_page) if total > 0 else 1

    # Convert to response models and generate presigned URLs for avatars
    items = []
    for bot in paginated_chatbots:
        bot_response = _limit_history_to_latest(ChatbotConfigResponse.from_orm(bot))
        # Generate presigned URL for avatar if it exists in the original model
        if bot.bot_avatar:
            bot_response.bot_avatar = await get_avatar_presigned_url(bot.bot_avatar)
        items.append(bot_response)

    response_data = ChatbotConfigListResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        pages=pages,
    )
    return success_response(data=response_data)


_CHATBOT_DEFAULTS_EXAMPLE = ChatbotConfigDefaultsResponse(
    base_defaults=_build_chatbot_defaults().base_defaults,
    config_defaults=_build_chatbot_defaults().config_defaults,
)

@router.get(
    "/defaults",
    response_model=ChatbotConfigDefaultsResponse,
    openapi_extra={
        "summary": "Get default chatbot values",
        "description": "Returns sample defaults so clients can pre-fill chatbot creation forms.",
        "responses": {
            200: {
                "description": "Defaults returned successfully",
                "content": {
                    "application/json": {
                        "example": {
                            "success": True,
                            "data": _CHATBOT_DEFAULTS_EXAMPLE.model_dump(),
                            "error": None,
                            "extra": None,
                        }
                    }
                },
            }
        },
    },
)
async def get_chatbot_defaults(
    _: User = Depends(get_current_user),
):
    """Return default values to help clients pre-fill chatbot creation forms."""
    defaults = _build_chatbot_defaults()
    return success_response(data=defaults)


@router.get("/{chatbot_id}", response_model=ChatbotConfigResponse)
async def get_chatbot(
    chatbot_id: int,
    db: AsyncSession = Depends(get_db),
    current_user_or_consumer: User | ChatConsumer = require_user_or_chat_consumer,
):
    """
    Get chatbot configuration by ID.
    Supports both User (Bearer token) and ChatConsumer (UUID) authentication.
    """
    chatbot_config_obj = await chatbot_config.get_with_relationships(db, chatbot_id)
    if not chatbot_config_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Chatbot not found"
        )
    
    # Extract tenant_id and consumer info based on authentication type
    if isinstance(current_user_or_consumer, ChatConsumer):
        tenant_id = current_user_or_consumer.tenant_id
    else:  # User
        tenant_id = current_user_or_consumer.tenant_id

    # Verify tenant ownership
    if chatbot_config_obj.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot access chatbot from different tenant",
        )

    # Get tenant details
    tenant = await db.get(Tenant, tenant_id)
    tenant_name = tenant.name if tenant else None
    tenant_logo = tenant.logo_url if tenant else None

    # Convert to response model and generate presigned URL for avatar
    response_data = _limit_history_to_latest(ChatbotConfigResponse.from_orm(chatbot_config_obj))
    
    # Add tenant details to response
    response_data.tenant_name = tenant_name
    response_data.tenant_logo = tenant_logo

    # Generate presigned URL for avatar if it exists in the original model
    if chatbot_config_obj.bot_avatar:
        response_data.bot_avatar = await get_avatar_presigned_url(chatbot_config_obj.bot_avatar)

    return success_response(data=response_data)


@router.put(
    "/{chatbot_id}",
    response_model=ChatbotConfigResponse,
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": ChatbotConfigUpdate.model_json_schema(),
                    "example": {
                        "name": "My Bot",
                        "title": "Customer Support",
                        "embedding_model_id": 1,
                        "chunk_size": 1000
                    }
                },
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": [],
                        "properties": {
                            "chatbot_config_body": {
                                "type": "string",
                                "description": "Chatbot configuration as JSON string (optional)",
                                "example": '{"name": "My Bot", "title": "Customer Support"}'
                            },
                            "avatar": {
                                "type": "string",
                                "format": "binary",
                                "description": "Optional avatar image file (jpg, jpeg, png, gif, webp, max 5MB)"
                            }
                        }
                    }
                }
            }
        }
    }
)
async def update_chatbot(
    chatbot_id: int,
    request: Request,
    avatar: UploadFile | None = File(None, description="Optional avatar image file (jpg, jpeg, png, gif, webp, max 5MB)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Update chatbot configuration.
    
    Supports both JSON body and optional avatar file upload.
    When avatar is provided, use multipart/form-data format.
    
    **Example with avatar only (multipart/form-data):**
    ```bash
    curl -X PUT "http://localhost:8000/api/v1/chatbots/1" \\
      -H "Authorization: Bearer YOUR_TOKEN" \\
      -F "avatar=@avatar.png"
    ```
    
    **Example with avatar and config (multipart/form-data):**
    ```bash
    curl -X PUT "http://localhost:8000/api/v1/chatbots/1" \
      -H "Authorization: Bearer YOUR_TOKEN" \
      -F 'chatbot_config_body={"name": "My Bot", "title": "Customer Support", ...}' \
      -F "avatar=@avatar.png"
    ```
    
    **Example without avatar (application/json):**
    ```bash
    curl -X PUT "http://localhost:8000/api/v1/chatbots/1" \
      -H "Authorization: Bearer YOUR_TOKEN" \
      -H "Content-Type: application/json" \
      -d '{
        "name": "My Bot",
        "title": "Customer Support",
        "embedding_model_id": 1,
        "chunk_size": 1000
      }'
    ```
    
    If avatar is provided, it will be uploaded to S3 and the S3 URL will be saved.
    If chatbot already has an avatar, the old one will be replaced.
    Note: chatbot_config_body is optional in multipart/form-data requests.
    """
    # Get existing chatbot
    chatbot_config_obj = await chatbot_config.get(db, chatbot_id)
    if not chatbot_config_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Chatbot not found"
        )

    # Verify tenant ownership
    if chatbot_config_obj.tenant_id != current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot update chatbot from different tenant",
        )

    content_type = request.headers.get("content-type", "").lower()
    
    # Parse request based on content type
    if "multipart/form-data" in content_type:
        # Handle multipart/form-data
        form = await request.form()
        chatbot_config_json = form.get("chatbot_config_body")
        
        # Parse JSON string if provided, otherwise create empty update object
        if chatbot_config_json:
            try:
                if isinstance(chatbot_config_json, str):
                    config_dict = json.loads(chatbot_config_json)
                else:
                    config_dict = json.loads(str(chatbot_config_json))
                chatbot_config_update = ChatbotConfigUpdate(**config_dict)
            except (json.JSONDecodeError, ValueError) as e:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid JSON in chatbot_config_body: {str(e)}",
                )
        else:
            # No config body provided, create empty update object (only avatar will be updated if provided)
            chatbot_config_update = ChatbotConfigUpdate()
        
        # Get avatar from form if not already provided
        if not avatar:
            avatar_file = form.get("avatar")
            if avatar_file and isinstance(avatar_file, UploadFile):
                avatar = avatar_file
    else:
        # Handle application/json
        try:
            body = await request.json()
            chatbot_config_update = ChatbotConfigUpdate(**body)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid request body: {str(e)}",
            )

    # Check if name is being updated and if it conflicts
    if chatbot_config_update.name and chatbot_config_update.name != chatbot_config_obj.name:
        existing_chatbots = await chatbot_config.list_by_tenant(
            db, chatbot_config_obj.tenant_id
        )
        for bot in existing_chatbots:
            if bot.id != chatbot_id and bot.name == chatbot_config_update.name:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Chatbot with name '{chatbot_config_update.name}' already exists for this tenant",
                )

    # Handle avatar upload if provided
    if avatar:
        try:
            # Validate file extension
            file_extension = os.path.splitext(avatar.filename)[1].lower()
            if file_extension not in settings.ALLOWED_AVATAR_TYPES:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Avatar file type {file_extension} not allowed. Supported types: {', '.join(settings.ALLOWED_AVATAR_TYPES)}",
                )

            # Read file content
            file_content = await avatar.read()
            file_size = len(file_content)

            # Validate file size
            max_size = settings.MAX_AVATAR_SIZE_MB * 1024 * 1024
            if file_size > max_size:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"Avatar file size exceeds {settings.MAX_AVATAR_SIZE_MB}MB limit",
                )

            # Delete old avatar if it exists - always attempt deletion regardless of format
            if chatbot_config_obj.bot_avatar:
                logger.info(f"Attempting to delete old avatar: {chatbot_config_obj.bot_avatar}")
                deleted = await delete_avatar(chatbot_config_obj.bot_avatar)
                if deleted:
                    logger.info(f"Successfully deleted old avatar: {chatbot_config_obj.bot_avatar}")
                else:
                    logger.warning(
                        f"Failed to delete old avatar (will continue with upload anyway): "
                        f"{chatbot_config_obj.bot_avatar}"
                    )

            # Upload new avatar to S3
            avatar_url = await upload_avatar(
                file_content=file_content,
                file_name=avatar.filename,
                tenant_id=chatbot_config_obj.tenant_id,
                file_size=file_size,
            )
            chatbot_config_update.bot_avatar = avatar_url
        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            )
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to upload avatar: {str(e)}",
            )

    updated_chatbot = await chatbot_config.update(db, chatbot_id, chatbot_config_update)
    if not updated_chatbot:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Chatbot not found"
        )

    response_data = _limit_history_to_latest(ChatbotConfigResponse.from_orm(updated_chatbot))
    if updated_chatbot.bot_avatar:
        response_data.bot_avatar = await get_avatar_presigned_url(updated_chatbot.bot_avatar)
    return success_response(data=response_data)


@router.delete("/{chatbot_id}", status_code=status.HTTP_200_OK)
async def delete_chatbot(
    chatbot_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete chatbot configuration"""
    # Get existing chatbot
    chatbot_config_obj = await chatbot_config.get(db, chatbot_id)
    if not chatbot_config_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Chatbot not found"
        )

    # Verify tenant ownership
    if chatbot_config_obj.tenant_id != current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot delete chatbot from different tenant",
        )

    deleted = await chatbot_config.delete(db, chatbot_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Chatbot not found"
        )

    return success_response(
        data={"message": "Chatbot deleted successfully"},
        status_code=status.HTTP_200_OK,
    )


@router.post("/{chatbot_id}/set-default", response_model=ChatbotConfigResponse)
async def set_default_chatbot(
    chatbot_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Set a chatbot as the default for the tenant"""
    # Get existing chatbot
    chatbot_config_obj = await chatbot_config.get(db, chatbot_id)
    if not chatbot_config_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Chatbot not found"
        )

    # Verify tenant ownership
    if chatbot_config_obj.tenant_id != current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot set default chatbot for different tenant",
        )

    updated_chatbot = await chatbot_config.set_default(
        db, current_user.tenant_id, chatbot_id
    )
    if not updated_chatbot:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Chatbot not found"
        )

    response_data = _limit_history_to_latest(ChatbotConfigResponse.from_orm(updated_chatbot))

    return success_response(data=response_data)

