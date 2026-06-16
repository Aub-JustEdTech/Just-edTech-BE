"""PDF document processor"""

import logging
from pathlib import Path
from typing import Any

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

try:
    import PyPDF2
except ImportError:
    PyPDF2 = None

from app.core.config import settings
from app.services.document_processing.base import DocumentProcessor
from app.services.document_processing.image_extractor import ImageExtractor

logger = logging.getLogger(__name__)


class PDFProcessor(DocumentProcessor):
    """Process PDF documents using PyMuPDF (fitz) for better text and image extraction"""

    supported_extensions = [".pdf"]
    supported_mime_types = ["application/pdf"]

    def __init__(self):
        """Initialize PDF processor with optional image extractor"""
        self.image_extractor: ImageExtractor | None = None

        if getattr(settings, "ENABLE_IMAGE_EXTRACTION", True):
            self.image_extractor = ImageExtractor()
        else:
            logger.info("PDF image extraction is disabled via settings.")

    def extract_text(self, file_path: str) -> str:
        """Extract text from PDF using PyMuPDF (preferred) or PyPDF2 (fallback)"""
        # Prefer PyMuPDF for better text extraction
        if fitz is not None:
            try:
                text = ""
                doc = fitz.open(file_path)
                for page in doc:
                    text += page.get_text() + "\n\n"
                doc.close()
                logger.info(f"Extracted {len(text)} characters from PDF using PyMuPDF")
                return text.strip()
            except Exception as e:
                logger.warning(f"PyMuPDF text extraction failed: {e}, trying PyPDF2")
                # Fall through to PyPDF2

        # Fallback to PyPDF2
        if PyPDF2 is None:
            raise ImportError(
                "Neither PyMuPDF nor PyPDF2 is installed. "
                "Install at least one: pip install PyMuPDF or pip install PyPDF2"
            )

        try:
            text = ""
            with open(file_path, "rb") as file:
                pdf_reader = PyPDF2.PdfReader(file)
                for page in pdf_reader.pages:
                    text += page.extract_text() + "\n\n"
            logger.info(f"Extracted {len(text)} characters from PDF using PyPDF2")
            return text.strip()
        except Exception as e:
            logger.error(f"Error extracting text from PDF: {e}")
            raise

    def extract_text_by_page(self, file_path: str) -> list[str]:
        """
        Extract text from a PDF as a list of page strings (1:1 with PDF pages).

        This is used to preserve `page_number` metadata for downstream chunking/citations.
        """
        # Prefer PyMuPDF for more accurate per-page extraction
        if fitz is not None:
            try:
                doc = fitz.open(file_path)
                pages: list[str] = []
                for page in doc:
                    pages.append((page.get_text() or "").strip())
                doc.close()
                return pages
            except Exception as e:
                logger.warning(f"PyMuPDF per-page extraction failed: {e}, trying PyPDF2")

        # Fallback to PyPDF2
        if PyPDF2 is None:
            raise ImportError(
                "Neither PyMuPDF nor PyPDF2 is installed. "
                "Install at least one: pip install PyMuPDF or pip install PyPDF2"
            )

        try:
            pages: list[str] = []
            with open(file_path, "rb") as file:
                pdf_reader = PyPDF2.PdfReader(file)
                for page in pdf_reader.pages:
                    pages.append((page.extract_text() or "").strip())
            return pages
        except Exception as e:
            logger.error(f"Error extracting per-page text from PDF: {e}")
            raise

    def extract_metadata(self, file_path: str) -> dict[str, Any]:
        """Extract PDF metadata using PyMuPDF (preferred) or PyPDF2 (fallback)"""
        # Prefer PyMuPDF for better metadata extraction
        if fitz is not None:
            try:
                doc = fitz.open(file_path)
                metadata = doc.metadata
                page_count = len(doc)
                doc.close()

                result = {
                    "page_count": page_count,
                    "author": metadata.get("author", ""),
                    "title": metadata.get("title", ""),
                    "subject": metadata.get("subject", ""),
                    "creator": metadata.get("creator", ""),
                    "producer": metadata.get("producer", ""),
                }
                return result
            except Exception as e:
                logger.warning(f"PyMuPDF metadata extraction failed: {e}, trying PyPDF2")
                # Fall through to PyPDF2

        # Fallback to PyPDF2
        if PyPDF2 is None:
            return {"page_count": 0}

        try:
            with open(file_path, "rb") as file:
                pdf_reader = PyPDF2.PdfReader(file)
                metadata = {
                    "page_count": len(pdf_reader.pages),
                    "author": pdf_reader.metadata.get("/Author", "")
                    if pdf_reader.metadata
                    else "",
                    "title": pdf_reader.metadata.get("/Title", "")
                    if pdf_reader.metadata
                    else "",
                }
            return metadata
        except Exception as e:
            logger.error(f"Error extracting PDF metadata: {e}")
            return {"page_count": 0}

    def extract_images_with_context(
        self, file_path: str, pdf_name: str | None = None, context_chars: int = 500
    ) -> list[dict[str, Any]]:
        """
        Extract images from PDF with surrounding text context.

        Args:
            file_path: Path to the PDF file
            pdf_name: Name of the PDF (without extension) for naming saved images.
                     If None, uses the filename from file_path.
            context_chars: Number of characters to extract before and after each image

        Returns:
            List of dictionaries containing image metadata with surrounding text context
        """
        if not getattr(settings, "ENABLE_IMAGE_EXTRACTION", True):
            logger.info("Skipping PDF image extraction (disabled in settings).")
            return []

        if fitz is None:
            logger.warning(
                "PyMuPDF (fitz) is not installed. Image extraction requires PyMuPDF. "
                "Install it with: pip install PyMuPDF"
            )
            return []

        if self.image_extractor is None:
            logger.warning(
                "ImageExtractor is not initialized; skipping PDF image extraction."
            )
            return []

        # Get PDF name from path if not provided
        if pdf_name is None:
            pdf_name = Path(file_path).stem

        try:
            # Extract images
            images = self.image_extractor.extract_images(file_path, pdf_name)

            # Filter out logos and icons
            filtered_images = self.image_extractor.filter_logos_and_icons(images)

            # Extract surrounding text context for each image
            if filtered_images:
                filtered_images = self._add_surrounding_text_context(
                    file_path, filtered_images, context_chars
                )

            logger.info(
                f"Extracted {len(filtered_images)} images from PDF with context "
                f"(filtered {len(images) - len(filtered_images)} logos/icons)"
            )

            return filtered_images
        except Exception as e:
            logger.error(f"Error extracting images from PDF: {e}")
            # Don't raise - image extraction failure shouldn't break text extraction
            return []

    def extract_images(
        self, file_path: str, pdf_name: str | None = None
    ) -> list[dict[str, Any]]:
        """
        Extract images from PDF (backward compatibility method).

        Args:
            file_path: Path to the PDF file
            pdf_name: Name of the PDF (without extension) for naming saved images.
                     If None, uses the filename from file_path.

        Returns:
            List of dictionaries containing image metadata
        """
        # Use the new method with default context
        return self.extract_images_with_context(file_path, pdf_name, context_chars=500)

    def _add_surrounding_text_context(
        self,
        file_path: str,
        images: list[dict[str, Any]],
        context_chars: int = 500,
    ) -> list[dict[str, Any]]:
        """
        Add surrounding text context to image metadata.

        Args:
            file_path: Path to the PDF file
            images: List of image metadata dictionaries
            context_chars: Number of characters to extract before and after each image

        Returns:
            List of image metadata dictionaries with surrounding text added
        """
        if fitz is None:
            return images

        try:
            doc = fitz.open(file_path)
            
            # Extract text blocks with positions for each page
            page_text_blocks = {}
            for page_num in range(len(doc)):
                page = doc[page_num]
                # Get text blocks with their positions
                blocks = page.get_text("dict")
                page_text_blocks[page_num] = blocks.get("blocks", [])

            # For each image, find surrounding text using actual image position
            for image in images:
                page_num = image.get("page_number", 1) - 1  # Convert to 0-indexed
                if page_num < 0 or page_num >= len(doc):
                    continue

                page = doc[page_num]
                
                # Get all text from the page
                page_text = page.get_text()
                
                # Extract text blocks with positions
                text_blocks = []
                for block in page_text_blocks.get(page_num, []):
                    if "lines" in block:
                        for line in block["lines"]:
                            for span in line.get("spans", []):
                                text = span.get("text", "").strip()
                                if text:
                                    bbox = span.get("bbox", [0, 0, 0, 0])
                                    text_blocks.append({
                                        "text": text,
                                        "bbox": bbox,
                                        "y_mid": (bbox[1] + bbox[3]) / 2,  # Middle Y position
                                    })

                # Get image position (if available)
                image_y = None
                if "position" in image and image["position"]:
                    image_y = image["position"].get("y", None)
                
                # Sort text blocks by Y position (top to bottom)
                text_blocks.sort(key=lambda b: b["y_mid"])
                
                # Find text blocks near the image position
                if image_y is not None and text_blocks:
                    # Find the index where image would be inserted
                    image_block_idx = 0
                    for i, block in enumerate(text_blocks):
                        if block["y_mid"] > image_y:
                            image_block_idx = i
                            break
                        image_block_idx = i + 1
                    
                    # Extract context around the image position
                    # Get blocks before and after the image
                    context_blocks_before = []
                    context_blocks_after = []
                    
                    # Collect blocks before image
                    chars_before = 0
                    for i in range(image_block_idx - 1, -1, -1):
                        block_text = text_blocks[i]["text"]
                        if chars_before + len(block_text) <= context_chars:
                            context_blocks_before.insert(0, block_text)
                            chars_before += len(block_text) + 1
                        else:
                            # Take partial text to fill remaining chars
                            remaining = context_chars - chars_before
                            if remaining > 0:
                                context_blocks_before.insert(0, block_text[-remaining:])
                            break
                    
                    # Collect blocks after image
                    chars_after = 0
                    for i in range(image_block_idx, len(text_blocks)):
                        block_text = text_blocks[i]["text"]
                        if chars_after + len(block_text) <= context_chars:
                            context_blocks_after.append(block_text)
                            chars_after += len(block_text) + 1
                        else:
                            # Take partial text to fill remaining chars
                            remaining = context_chars - chars_after
                            if remaining > 0:
                                context_blocks_after.append(block_text[:remaining])
                            break
                    
                    text_before = " ".join(context_blocks_before).strip()
                    text_after = " ".join(context_blocks_after).strip()
                else:
                    # Fallback: use image index to estimate position
                    # Split page text into words to estimate position
                    words = page_text.split()
                    total_words = len(words)
                    
                    # Estimate image position based on image index and total images on page
                    images_on_page = [img for img in images if img.get("page_number", 1) - 1 == page_num]
                    image_idx_on_page = images_on_page.index(image) if image in images_on_page else 0
                    total_images_on_page = len(images_on_page)
                    
                    # Distribute images evenly across page
                    if total_images_on_page > 1:
                        image_position_ratio = (image_idx_on_page + 1) / (total_images_on_page + 1)
                    else:
                        image_position_ratio = 0.5  # Middle if only one image
                    
                    # Calculate word indices for context
                    context_words = context_chars // 10  # Rough estimate: ~10 chars per word
                    start_word = max(0, int(total_words * image_position_ratio) - context_words)
                    end_word = min(total_words, int(total_words * image_position_ratio) + context_words)
                    
                    # Extract text before and after
                    text_before_words = words[max(0, start_word - context_words):start_word]
                    text_after_words = words[end_word:min(total_words, end_word + context_words)]
                    
                    text_before = " ".join(text_before_words)[-context_chars:]
                    text_after = " ".join(text_after_words)[:context_chars]
                
                image["surrounding_text_before"] = text_before
                image["surrounding_text_after"] = text_after
                image["caption"] = f"{text_before} [IMAGE] {text_after}".strip()

            doc.close()
            return images

        except Exception as e:
            logger.warning(f"Error adding surrounding text context: {e}")
            # Return images without context if extraction fails
            return images

    def validate(self, file_path: str) -> bool:
        """Validate PDF file"""
        # Try PyMuPDF first
        if fitz is not None:
            try:
                doc = fitz.open(file_path)
                doc.close()
                return True
            except Exception:
                pass

        # Fallback to PyPDF2
        if PyPDF2 is not None:
            try:
                with open(file_path, "rb") as file:
                    PyPDF2.PdfReader(file)
                return True
            except Exception:
                return False

        # If neither library is available, we can't validate
        return False
