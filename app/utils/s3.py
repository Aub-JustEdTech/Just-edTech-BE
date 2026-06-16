"""
S3 storage manager for document uploads.
"""

import asyncio

import aioboto3
from botocore.exceptions import ClientError


class S3Manager:
    """
    Manages S3 operations for document storage.

    Supports async operations using aioboto3.
    """

    def __init__(
        self,
        bucket_name: str | None,
        region_name: str = "us-east-1",
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
    ):
        self.bucket_name = bucket_name
        self.region_name = region_name
        self.aws_access_key_id = aws_access_key_id
        self.aws_secret_access_key = aws_secret_access_key
        self.session = aioboto3.Session(
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            region_name=region_name,
        )

    async def upload_file_object(self, file_content: bytes, s3_key: str) -> str:
        """
        Upload a file object to S3.

        Args:
            file_content: File content as bytes
            s3_key: S3 object key (path within bucket)

        Returns:
            S3 URL of the uploaded file

        Raises:
            RuntimeError: If upload fails
        """
        if not self.bucket_name:
            raise ValueError("S3 bucket name is not configured")

        try:
            async with self.session.client("s3") as s3_client:
                await s3_client.put_object(
                    Bucket=self.bucket_name,
                    Key=s3_key,
                    Body=file_content,
                )
            return f"s3://{self.bucket_name}/{s3_key}"
        except ClientError as e:
            raise RuntimeError(f"Failed to upload file to S3: {e}") from e
        except Exception as e:
            raise RuntimeError(f"Unexpected error during S3 upload: {e}") from e

    async def upload_fileobj_stream(self, fileobj, s3_key: str) -> str:
        """
        Stream upload a file-like object to S3 without loading entire file into memory.

        Args:
            fileobj: File-like object (e.g., SpooledTemporaryFile from UploadFile.file)
            s3_key: S3 object key (path within bucket)

        Returns:
            S3 URL of the uploaded file

        Raises:
            RuntimeError: If upload fails
        """
        if not self.bucket_name:
            raise ValueError("S3 bucket name is not configured")

        try:
            async with self.session.client("s3") as s3_client:
                await s3_client.upload_fileobj(
                    fileobj,
                    self.bucket_name,
                    s3_key,
                )
            return f"s3://{self.bucket_name}/{s3_key}"
        except ClientError as e:
            raise RuntimeError(f"Failed to upload file to S3: {e}") from e
        except Exception as e:
            raise RuntimeError(f"Unexpected error during S3 upload: {e}") from e

    async def download_file_object(self, s3_key: str, local_path: str) -> None:
        """
        Download a file from S3 to local path.

        Args:
            s3_key: S3 object key (path within bucket)
            local_path: Local file path to save the downloaded file

        Raises:
            RuntimeError: If download fails
        """
        if not self.bucket_name:
            raise ValueError("S3 bucket name is not configured")

        try:
            async with self.session.client("s3") as s3_client:
                response = await s3_client.get_object(
                    Bucket=self.bucket_name,
                    Key=s3_key,
                )

                # Read the streaming body and write to file
                async with response["Body"] as stream:
                    content = await stream.read()

                # Write to local file
                await asyncio.to_thread(self._write_file, local_path, content)
        except ClientError as e:
            raise RuntimeError(f"Failed to download file from S3: {e}") from e
        except Exception as e:
            raise RuntimeError(f"Unexpected error during S3 download: {e}") from e

    @staticmethod
    def _write_file(path: str, content: bytes) -> None:
        """Helper to write file synchronously."""
        with open(path, "wb") as f:
            f.write(content)

    async def delete_file_object(self, s3_key: str) -> bool:
        """
        Delete a file from S3.

        Args:
            s3_key: S3 object key (path within bucket)

        Returns:
            True if successful, False otherwise

        Raises:
            RuntimeError: If deletion fails
        """
        if not self.bucket_name:
            raise ValueError("S3 bucket name is not configured")

        try:
            async with self.session.client("s3") as s3_client:
                await s3_client.delete_object(
                    Bucket=self.bucket_name,
                    Key=s3_key,
                )
            return True
        except ClientError as e:
            raise RuntimeError(f"Failed to delete file from S3: {e}") from e
        except Exception as e:
            raise RuntimeError(f"Unexpected error during S3 deletion: {e}") from e

    async def file_exists(self, s3_key: str) -> bool:
        """
        Check if a file exists in S3.

        Args:
            s3_key: S3 object key (path within bucket)

        Returns:
            True if file exists, False otherwise
        """
        if not self.bucket_name:
            raise ValueError("S3 bucket name is not configured")

        try:
            async with self.session.client("s3") as s3_client:
                await s3_client.head_object(
                    Bucket=self.bucket_name,
                    Key=s3_key,
                )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return False
            raise RuntimeError(f"Failed to check file existence in S3: {e}") from e
        except Exception as e:
            raise RuntimeError(f"Unexpected error checking S3 file: {e}") from e

    async def get_presigned_url(
        self,
        s3_key: str,
        expiration: int = 3600,
        http_method: str = "GET",
        response_content_type: str | None = None,
        response_content_disposition: str | None = None,
    ) -> str:
        """
        Generate a presigned URL for accessing an S3 object.

        Args:
            s3_key: S3 object key (path within bucket)
            expiration: URL expiration time in seconds (default: 1 hour)
            http_method: HTTP method (GET, PUT, etc.)

        Returns:
            Presigned URL string

        Raises:
            RuntimeError: If URL generation fails
        """
        if not self.bucket_name:
            raise ValueError("S3 bucket name is not configured")

        try:
            async with self.session.client("s3") as s3_client:
                params = {
                    "Bucket": self.bucket_name,
                    "Key": s3_key,
                }
                if response_content_type:
                    params["ResponseContentType"] = response_content_type
                if response_content_disposition:
                    params["ResponseContentDisposition"] = response_content_disposition

                url = await s3_client.generate_presigned_url(
                    ClientMethod="get_object" if http_method == "GET" else "put_object",
                    Params=params,
                    ExpiresIn=expiration,
                )
            return url
        except ClientError as e:
            raise RuntimeError(f"Failed to generate presigned URL: {e}") from e
        except Exception as e:
            raise RuntimeError(f"Unexpected error generating presigned URL: {e}") from e


def extract_s3_key_from_url(raw_url: str | None, bucket_name: str | None = None) -> str | None:
    """
    Extract S3 key from various URL formats.
    
    Handles:
    - s3://bucket/key -> key
    - https://bucket.s3.region.amazonaws.com/key -> key
    - https://s3.region.amazonaws.com/bucket/key -> key
    - Just the key itself
    
    Args:
        raw_url: S3 URL or key in various formats
        bucket_name: Optional bucket name to help extract key
        
    Returns:
        S3 key (path within bucket) or None if extraction fails
    """
    if not raw_url:
        return None
    
    raw_url = raw_url.strip()
    if not raw_url:
        return None
    
    # s3://bucket/key format
    if raw_url.startswith("s3://"):
        remainder = raw_url[5:]  # Remove "s3://"
        if "/" in remainder:
            _, key = remainder.split("/", 1)
            return key if key else None
        return None
    
    # HTTPS URL formats
    if raw_url.startswith("https://") or raw_url.startswith("http://"):
        # Format: https://bucket.s3.region.amazonaws.com/key
        # Format: https://s3.region.amazonaws.com/bucket/key
        if "amazonaws.com" in raw_url:
            parts = raw_url.split("amazonaws.com/")
            if len(parts) == 2:
                key = parts[1].split("?")[0]  # Remove query parameters
                # If bucket is in the path (s3.region.amazonaws.com/bucket/key), remove it
                if bucket_name and key.startswith(f"{bucket_name}/"):
                    key = key[len(f"{bucket_name}/"):]
                return key if key else None
        # Other HTTPS URLs - try to extract path
        try:
            from urllib.parse import urlparse
            parsed = urlparse(raw_url)
            path = parsed.path.lstrip("/")
            if bucket_name and path.startswith(f"{bucket_name}/"):
                path = path[len(f"{bucket_name}/"):]
            return path if path else None
        except Exception:
            return None
    
    # Assume it's already a key, but remove bucket prefix if present
    key = raw_url
    if bucket_name and key.startswith(f"{bucket_name}/"):
        key = key[len(f"{bucket_name}/"):]
    
    return key if key else None
