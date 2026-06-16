"""
Chat consumer authentication endpoints for lightweight chat users.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.chat_consumers import chat_consumer
from app.crud.tenants import tenant
from app.schemas.chat_consumers import (
    ChatConsumerRegisterRequest,
    ChatConsumerRegisterResponse,
)
from app.utils.dependencies import get_db
from app.utils.response import success_response

router = APIRouter()


@router.post("/register", response_model=ChatConsumerRegisterResponse)
async def register_chat_consumer(
    request: ChatConsumerRegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    """Register a new chat consumer with a tenant"""
    # Verify tenant exists
    db_tenant = await tenant.get(db, tenant_id=request.tenant_id)
    if not db_tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found"
        )

    # Create new chat consumer
    db_chat_consumer = await chat_consumer.create(db, chat_consumer_create=request)

    return success_response(
        data=ChatConsumerRegisterResponse(
            chat_consumer_uuid=db_chat_consumer.chat_consumer_uuid,
            tenant_id=db_chat_consumer.tenant_id,
        ),
        status_code=status.HTTP_201_CREATED,
    )
