"""
Image caption service using GPT-4o vision for generating context-aware captions.
"""

import base64
import logging
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)


class ImageCaptionService:
    """Service for generating image captions using GPT-4o vision"""

    def __init__(self):
        """Initialize the image caption service"""
        self.client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY) if settings.OPENAI_API_KEY else None
        if not self.client:
            logger.warning("OpenAI API key not configured. Image captioning will be disabled.")

    async def generate_caption(
        self,
        image_path: str,
        surrounding_text_before: str | None = None,
        surrounding_text_after: str | None = None,
    ) -> str:
        """
        Generate a caption for an image using GPT-4o vision.

        Args:
            image_path: Path to the image file
            surrounding_text_before: Text that appears before the image in the document
            surrounding_text_after: Text that appears after the image in the document

        Returns:
            Generated caption string
        """
        if not self.client:
            # Fallback to context-based caption if OpenAI is not available
            return self._create_context_caption(surrounding_text_before, surrounding_text_after)

        try:
            # Read and encode image
            image_data = self._encode_image(image_path)
            if not image_data:
                logger.warning(f"Could not read image from {image_path}")
                return self._create_context_caption(surrounding_text_before, surrounding_text_after)

            # Build prompt with context
            context_parts = []
            if surrounding_text_before:
                context_parts.append(f"Context before image: {surrounding_text_before}")
            if surrounding_text_after:
                context_parts.append(f"Context after image: {surrounding_text_after}")

            context_text = "\n".join(context_parts) if context_parts else "No surrounding context available."

            prompt = f"""Analyze this image from a document and generate a detailed, descriptive caption optimized for search and retrieval.

{context_text}

The caption should:
1. Describe what is shown in the image (tables, figures, diagrams, charts, graphs, illustrations, etc.)
2. Include specific details: data types, labels, categories, measurements, relationships, or key concepts visible
3. Incorporate relevant keywords and terminology from the surrounding text context
4. Mention any text, numbers, or labels visible within the image itself
5. Be comprehensive but structured (3-4 sentences recommended)
6. Focus on content that would help users find this image through text queries
7. Include domain-specific terms and technical vocabulary when relevant

Generate a detailed caption that captures both visual content and contextual meaning:"""

            # Call GPT-4o vision
            response = await self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{image_data}",
                                    "detail": "high",
                                },
                            },
                        ],
                    }
                ],
                max_tokens=400,  # Increased for more detailed captions
                temperature=0.2,  # Lower temperature for more consistent, detailed captions
            )

            caption = response.choices[0].message.content.strip()
            logger.info(f"Generated caption for {image_path}: {caption[:100]}...")
            return caption

        except Exception as e:
            logger.error(f"Error generating caption with GPT-4o: {e}", exc_info=True)
            # Fallback to context-based caption
            return self._create_context_caption(surrounding_text_before, surrounding_text_after)

    def _encode_image(self, image_path: str) -> str | None:
        """
        Encode image to base64.

        Args:
            image_path: Path to the image file

        Returns:
            Base64-encoded image string or None if error
        """
        try:
            with open(image_path, "rb") as image_file:
                return base64.b64encode(image_file.read()).decode("utf-8")
        except Exception as e:
            logger.error(f"Error encoding image {image_path}: {e}")
            return None

    def _create_context_caption(
        self,
        surrounding_text_before: str | None,
        surrounding_text_after: str | None,
    ) -> str:
        """
        Create a caption from surrounding text context when vision API is unavailable.

        Args:
            surrounding_text_before: Text before the image
            surrounding_text_after: Text after the image

        Returns:
            Context-based caption
        """
        parts = []
        if surrounding_text_before:
            # Take last 100 chars of text before
            parts.append(surrounding_text_before[-100:].strip())
        if surrounding_text_after:
            # Take first 100 chars of text after
            parts.append(surrounding_text_after[:100].strip())

        if parts:
            caption = " ".join(parts)
            # Truncate to reasonable length
            if len(caption) > 300:
                caption = caption[:297] + "..."
            return caption

        return "Image from document"
