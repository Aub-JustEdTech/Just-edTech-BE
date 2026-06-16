"""
Avatar upload utility for chatbot avatars.
Reuses S3 upload flow similar to document uploads.
"""

import os
import uuid
import logging

from app.core.config import settings
from app.utils.s3 import S3Manager

logger = logging.getLogger(__name__)


async def upload_avatar(
    file_content: bytes,
    file_name: str,
    tenant_id: int,
    file_size: int,
) -> str:
    """
    Upload an avatar image to S3 and return the S3 URL.

    Args:
        file_content: File content as bytes
        file_name: Original file name
        tenant_id: Tenant ID
        file_size: File size in bytes

    Returns:
        S3 URL of the uploaded avatar (format: s3://bucket/key)

    Raises:
        ValueError: If file type or size is invalid
        RuntimeError: If S3 upload fails
    """
    # 1. Validate file type and size
    file_extension = os.path.splitext(file_name)[1].lower()
    if file_extension not in settings.ALLOWED_AVATAR_TYPES:
        raise ValueError(
            f"File type {file_extension} is not allowed. "
            f"Supported types: {', '.join(settings.ALLOWED_AVATAR_TYPES)}"
        )
    
    if file_size > settings.MAX_AVATAR_SIZE_MB * 1024 * 1024:
        raise ValueError(
            f"File size exceeds {settings.MAX_AVATAR_SIZE_MB} MB limit."
        )

    # 2. Generate unique avatar ID and S3 path
    avatar_uuid = str(uuid.uuid4())
    s3_key = f"tenants/{tenant_id}/avatars/{avatar_uuid}{file_extension}"

    # 3. Upload to S3
    s3_manager = S3Manager(
        bucket_name=settings.S3_BUCKET_NAME,
        region_name=settings.S3_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    )
    
    await s3_manager.upload_file_object(file_content, s3_key)
    s3_url = f"s3://{settings.S3_BUCKET_NAME}/{s3_key}"

    logger.info(
        f"Uploaded avatar {avatar_uuid} for tenant {tenant_id} to {s3_key}"
    )

    return s3_url


async def delete_avatar(s3_url: str) -> bool:
    """
    Delete an avatar from S3.
    
    Attempts to extract S3 key from various URL formats and delete the file.
    Always attempts deletion regardless of URL format - handles errors gracefully.

    Args:
        s3_url: S3 URL of the avatar (can be in various formats:
                - s3://bucket/key
                - https://bucket.s3.region.amazonaws.com/key
                - https://s3.region.amazonaws.com/bucket/key
                - Just the key if it's already extracted)

    Returns:
        True if successful, False otherwise (never raises exceptions)
    """
    if not s3_url or not s3_url.strip():
        logger.warning("Empty S3 URL provided for deletion")
        return False

    s3_url = s3_url.strip()
    s3_key = None

    # Strategy 1: Standard s3://bucket/key format
    if s3_url.startswith("s3://"):
        try:
            url_parts = s3_url[5:].split("/", 1)  # Remove "s3://" and split
            if len(url_parts) == 2:
                bucket_name, key = url_parts
                # Use the key regardless of bucket name match (try to delete anyway)
                if key:
                    s3_key = key
                    logger.info(f"Extracted S3 key from s3:// format: {s3_key}")
        except Exception as e:
            logger.debug(f"Failed to parse s3:// format: {e}")

    # Strategy 2: HTTPS URL format (https://bucket.s3.region.amazonaws.com/key)
    if not s3_key and ("s3" in s3_url.lower() or "amazonaws.com" in s3_url.lower()):
        try:
            # Try to extract key from various HTTPS formats
            if "amazonaws.com" in s3_url:
                # Format: https://bucket.s3.region.amazonaws.com/key or https://s3.region.amazonaws.com/bucket/key
                parts = s3_url.split("amazonaws.com/")
                if len(parts) == 2:
                    # Get everything after amazonaws.com/
                    potential_key = parts[1].split("?")[0]  # Remove query parameters
                    if potential_key:
                        s3_key = potential_key
                        logger.info(f"Extracted S3 key from HTTPS format: {s3_key}")
        except Exception as e:
            logger.debug(f"Failed to parse HTTPS format: {e}")

    # Strategy 3: If it looks like a path (contains /), try using it as-is
    if not s3_key and "/" in s3_url:
        # If it contains tenants/avatars/, it's likely already a key
        if "tenants/" in s3_url or "avatars/" in s3_url:
            # Remove any protocol prefix
            key = s3_url.split("://")[-1] if "://" in s3_url else s3_url
            # Remove bucket name if present at the start
            if key.startswith(f"{settings.S3_BUCKET_NAME}/"):
                key = key[len(f"{settings.S3_BUCKET_NAME}/"):]
            s3_key = key
            logger.info(f"Extracted S3 key from path-like format: {s3_key}")

    # Strategy 4: If still no key, try using the URL as-is (might already be a key)
    if not s3_key:
        # Remove common prefixes
        key = s3_url
        if key.startswith("s3://"):
            key = key[5:]
        if "/" in key:
            # If it has a slash, take everything after the first slash (assuming bucket/key format)
            parts = key.split("/", 1)
            if len(parts) == 2:
                key = parts[1]
        s3_key = key
        logger.info(f"Using URL as S3 key (fallback): {s3_key}")

    if not s3_key:
        logger.warning(f"Could not extract S3 key from URL: {s3_url}")
        return False

    logger.info(f"Attempting to delete avatar from S3: key={s3_key}, original_url={s3_url}")

    s3_manager = S3Manager(
        bucket_name=settings.S3_BUCKET_NAME,
        region_name=settings.S3_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    )

    try:
        await s3_manager.delete_file_object(s3_key)
        logger.info(f"Successfully deleted avatar from S3: {s3_key}")
        return True
    except Exception as e:
        # Log error but don't raise - we want to continue with upload even if deletion fails
        logger.warning(
            f"Failed to delete avatar from S3 (key: {s3_key}, original_url: {s3_url}): {e}",
            exc_info=True
        )
        return False

