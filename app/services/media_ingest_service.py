"""Media ingest — turns uploaded audio/video and pasted links into Documents.

This exists because media and documents arrive in opposite orders. A PDF is
already text when it lands in S3, so the pipeline can start immediately. Media
is not text until something pays to transcribe it, and that takes minutes.

The school scraper solved this by transcribing FIRST and only then creating the
Document, so the pipeline always sees a ``.transcript`` envelope. Uploads
cannot do that — the bytes arrive before anything knows how long they are — so
the Document is created up front in a media state, and this module flips it to
``.transcript`` once the transcript exists. From that point the existing
pipeline runs unmodified.

S3 layout, mirroring the scraper so both ingests are inspectable the same way:

    tenants/{tenant_id}/media/{doc_uuid}/source{ext}     (uploads only)
    tenants/{tenant_id}/media/{doc_uuid}/transcript.txt  (the pipeline input)

One transcript artifact, not two: the ``.txt`` carries timestamps and speaker
labels in each line's prefix, so it is both the readable copy and the machine
input. See ``TranscriptResult.to_text_document``.
"""

from __future__ import annotations

import logging
import os
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.documents import Document, ProcessingStatus
from app.models.processing_jobs import DocumentProcessingJob, JobStatus
from app.services.transcription.schemas import TranscriptResult
from app.services.transcription.youtube import extract_youtube_id
from app.utils.s3 import S3Manager

logger = logging.getLogger(__name__)

# Placeholder document_type values held between Document creation and the
# arrival of a transcript. Both carry a leading dot because the pipeline builds
# "{uuid}{document_type}" as a filename and ProcessorFactory keys on the
# suffix — a bare "youtube" would silently become an extensionless temp file.
DOCTYPE_YOUTUBE = ".youtube"
DOCTYPE_MEDIA_LINK = ".medialink"

SOURCE_TYPE_MEDIA_UPLOAD = "media_upload"
SOURCE_TYPE_MEDIA_LINK = "media_link"

# document_type values that mean "not transcribed yet".
PENDING_MEDIA_DOCTYPES = frozenset(
    {DOCTYPE_YOUTUBE, DOCTYPE_MEDIA_LINK, *settings.ALLOWED_MEDIA_TYPES}
)


def is_media_extension(file_name: str) -> bool:
    return os.path.splitext(file_name)[1].lower() in settings.ALLOWED_MEDIA_TYPES


def is_youtube_url(url: str) -> bool:
    return extract_youtube_id(url) is not None


class MediaIngestService:
    """Creates and finalises Documents whose source is audio or video."""

    def __init__(self):
        self.s3 = S3Manager(
            bucket_name=settings.S3_BUCKET_NAME,
            region_name=settings.S3_REGION,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        )

    @staticmethod
    def _key_prefix(tenant_id: int, doc_uuid: str) -> str:
        return f"tenants/{tenant_id}/media/{doc_uuid}"

    # ------------------------------------------------------------------
    # Entry points
    # ------------------------------------------------------------------

    async def create_upload(
        self,
        db: AsyncSession,
        *,
        fileobj,
        file_name: str,
        tenant_id: int,
        file_size: int,
        upload_batch_id: int | None = None,
    ) -> tuple[Document, DocumentProcessingJob]:
        """Stream an uploaded media file to S3 and register it for transcription."""
        ext = os.path.splitext(file_name)[1].lower()
        if ext not in settings.ALLOWED_MEDIA_TYPES:
            raise ValueError(f"File type {ext} is not a supported media type.")
        max_bytes = settings.MAX_MEDIA_FILE_SIZE_MB * 1024 * 1024
        if file_size > max_bytes:
            raise ValueError(
                f"Media size exceeds {settings.MAX_MEDIA_FILE_SIZE_MB} MB limit."
            )

        doc_uuid = str(uuid.uuid4())
        s3_key = f"{self._key_prefix(tenant_id, doc_uuid)}/source{ext}"
        await self.s3.upload_fileobj_stream(fileobj, s3_key)

        document = Document(
            name=file_name,
            doc_id=doc_uuid,
            s3_url=f"s3://{settings.S3_BUCKET_NAME}/{s3_key}",
            tenant_id=tenant_id,
            document_type=ext,
            processing_status=ProcessingStatus.PENDING,
            file_size_bytes=file_size,
            upload_batch_id=upload_batch_id,
            source_type=SOURCE_TYPE_MEDIA_UPLOAD,
            source_metadata={
                "media_type": "video" if ext in (".mp4", ".mov", ".webm") else "audio",
                # Retained so playback survives the s3_url being repointed at
                # the transcript envelope once transcription finishes.
                "s3_key_raw": s3_key,
                "original_extension": ext,
            },
        )
        return await self._persist(db, document, processor_type=ext)

    async def create_link(
        self,
        db: AsyncSession,
        *,
        url: str,
        tenant_id: int,
        name: str | None = None,
    ) -> tuple[Document, DocumentProcessingJob]:
        """Register a YouTube or direct media URL for transcription.

        Nothing is uploaded here: there is no file yet. ``s3_url`` stays NULL
        until the transcript envelope exists, which the unique index permits
        because Postgres does not treat NULLs as equal.
        """
        url = url.strip()
        if not url:
            raise ValueError("A media URL is required.")

        youtube_id = extract_youtube_id(url)
        if youtube_id:
            if not settings.transcription_youtube_enabled:
                raise ValueError("YouTube ingestion is currently disabled.")
            document_type = DOCTYPE_YOUTUBE
            media_type = "youtube"
            default_name = f"YouTube video {youtube_id}"
        else:
            if not url.lower().startswith(("http://", "https://")):
                raise ValueError("Media URL must start with http:// or https://")
            document_type = DOCTYPE_MEDIA_LINK
            media_type = "url"
            default_name = url.rsplit("/", 1)[-1] or url

        doc_uuid = str(uuid.uuid4())
        document = Document(
            name=name or default_name,
            doc_id=doc_uuid,
            s3_url=None,
            tenant_id=tenant_id,
            document_type=document_type,
            processing_status=ProcessingStatus.PENDING,
            source_type=SOURCE_TYPE_MEDIA_LINK,
            source_metadata={
                "media_type": media_type,
                "source_media_url": url,
                "youtube_id": youtube_id,
            },
        )
        return await self._persist(db, document, processor_type=document_type)

    async def _persist(
        self,
        db: AsyncSession,
        document: Document,
        *,
        processor_type: str,
    ) -> tuple[Document, DocumentProcessingJob]:
        db.add(document)
        await db.commit()
        await db.refresh(document)

        job = DocumentProcessingJob(
            document_id=document.id,
            status=JobStatus.PENDING,
            processor_type=processor_type,
        )
        db.add(job)
        await db.commit()
        await db.refresh(job)

        logger.info(
            "Registered media document %s (%s) for tenant %s",
            document.id,
            document.document_type,
            document.tenant_id,
        )
        return document, job

    # ------------------------------------------------------------------
    # Finalisation
    # ------------------------------------------------------------------

    async def resolve_media_source(self, document: Document) -> str:
        """The URL a transcription provider should fetch.

        For links this is the original URL. For uploads it is a presigned S3
        URL, which is what lets ``url_direct`` mode work for uploads too: the
        provider downloads the file itself and the worker never holds the
        bytes or spends the disk.
        """
        meta = document.source_metadata or {}
        if document.source_type == SOURCE_TYPE_MEDIA_LINK:
            url = meta.get("source_media_url")
            if not url:
                raise ValueError(
                    f"Document {document.id} has no source_media_url to transcribe"
                )
            return url

        s3_key = meta.get("s3_key_raw")
        if not s3_key:
            raise ValueError(f"Document {document.id} has no stored media to transcribe")
        return await self.s3.get_presigned_url(
            s3_key,
            expiration=settings.MEDIA_INGEST_PRESIGN_EXPIRY_SECONDS,
        )

    async def store_transcript(
        self,
        db: AsyncSession,
        document: Document,
        transcript: TranscriptResult,
    ) -> str:
        """Write the transcript to S3 as one text file and repoint the Document.

        A single artifact: the ``.txt`` is both the human-readable copy and the
        pipeline's input. Timestamps and speaker labels live in each line's
        prefix, so nothing is lost by not keeping a structured sidecar — see
        ``TranscriptResult.to_text_document``.
        """
        prefix = self._key_prefix(document.tenant_id, document.doc_id)

        transcript_key = f"{prefix}/transcript.txt"
        transcript_url = await self.s3.upload_file_object(
            transcript.to_text_document().encode("utf-8"),
            transcript_key,
        )

        meta = dict(document.source_metadata or {})
        meta.update(
            {
                "s3_key_text": transcript_key,
                "transcript_source": transcript.source,
                "speech_model": transcript.speech_model,
                "caption_kind": transcript.caption_kind,
                "duration_seconds": transcript.duration_seconds,
            }
        )

        document.s3_url = transcript_url
        # The flip that hands the item to the normal pipeline: from here on it
        # is an ordinary transcript document and every downstream stage — chunk,
        # embed, store — runs unchanged.
        document.document_type = ".transcript"
        document.source_metadata = meta
        if transcript.source_size_bytes and not document.file_size_bytes:
            document.file_size_bytes = transcript.source_size_bytes

        await db.commit()
        await db.refresh(document)

        logger.info(
            "Stored transcript for document %s (%s segments, source=%s)",
            document.id,
            len(transcript.segments),
            transcript.source,
        )
        return transcript_url


media_ingest_service = MediaIngestService()
