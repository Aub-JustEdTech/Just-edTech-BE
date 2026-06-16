"""
API Key management endpoints.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.api_keys import api_keys
from app.utils.api_keys import generate_api_key
from app.utils.dependencies import (
    get_current_tenant_admin,
    get_db,
    require_chat_consumer,
)
from app.utils.response import success_response

router = APIRouter()


@router.post("/", summary="Create API key")
async def create_api_key(
    db: AsyncSession = Depends(get_db), current_user=Depends(get_current_tenant_admin)
):
    key = generate_api_key()
    record = await api_keys.create(
        db,
        tenant_id=current_user.tenant_id,
        key=key,
    )
    return success_response(
        data={"id": record.id, "key": key},
        status_code=status.HTTP_201_CREATED,
    )


@router.get("/", summary="List API keys")
async def list_api_keys(
    db: AsyncSession = Depends(get_db), current_user=Depends(get_current_tenant_admin)
):
    records = await api_keys.list_by_tenant(db, current_user.tenant_id)
    return success_response(
        data=[
            {
                "id": r.id,
                "key": r.key,
                "created_at": r.created_at,
                "updated_at": r.updated_at,
            }
            for r in records
        ]
    )


@router.get("/latest", summary="Get latest API key for chat consumer")
async def get_latest_api_key(
    db: AsyncSession = Depends(get_db),
    chat_consumer=require_chat_consumer,
):
    """
    Get the latest API key for the tenant associated with the chat consumer.

    **Use Case:**
    This endpoint allows a chat widget (Chat Consumer) to bootstrap itself.
    If the widget has a valid `chat_consumer_uuid` but is missing the `api_key`
    required for other operations, it can call this endpoint to retrieve it.

    **Authentication:**
    Requires `X-Chat-Consumer-UUID` header or `chat_consumer_uuid` query parameter.

    **Returns:**
    - The most recently created API key for the tenant.
    - 404 if no API keys exist for the tenant.
    """
    key_record = await api_keys.get_latest_by_tenant(db, chat_consumer.tenant_id)
    if not key_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No API key found for this tenant",
        )

    return success_response(
        data={
            "id": key_record.id,
            "key": key_record.key,
            "created_at": key_record.created_at,
        }
    )


@router.post("/{api_key_id}/revoke", summary="Revoke API key")
async def revoke_api_key(
    api_key_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_tenant_admin),
):
    count = await api_keys.revoke(db, api_key_id, current_user.tenant_id)
    if count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="API key not found"
        )
    return success_response(data={"message": "API key revoked successfully"})
