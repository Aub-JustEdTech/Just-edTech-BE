"""
Image extraction module for PDF documents using PyMuPDF (fitz).

Extracts raster images from PDFs, filters out small images, logos, and icons,
and saves them to the filesystem with metadata tracking.
"""

import io
import logging
import os
from pathlib import Path
from typing import Any

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

from app.core.config import settings

logger = logging.getLogger(__name__)


class ImageExtractor:
    """Extract raster images from PDF documents"""

    def __init__(
        self,
        min_width: int = 100,
        min_height: int = 100,
        image_storage_dir: str | None = None,
        extract_vectors: bool = True,
        render_dpi: int = 300,
        header_footer_threshold: float = 0.15,
        similarity_threshold: float = 0.3,
        extract_svg: bool = False,
    ):
        """
        Initialize image extractor.

        Args:
            min_width: Minimum image width in pixels (default: 100)
            min_height: Minimum image height in pixels (default: 100)
            image_storage_dir: Directory to save extracted images
            extract_vectors: (unused) previously controlled vector graphics extraction
            render_dpi: (unused) previously used DPI for vector rendering
            header_footer_threshold: (unused) previously used for header/footer detection
            similarity_threshold: (unused) previously used for repetitive element detection
            extract_svg: (unused) previously controlled SVG extraction
        """
        if fitz is None:
            raise ImportError(
                "PyMuPDF (fitz) is required for image extraction. "
                "Install it with: pip install PyMuPDF"
            )

        self.min_width = min_width
        self.min_height = min_height
        # Kept for backwards compatibility but no longer used for extraction
        self.extract_vectors = extract_vectors
        self.render_dpi = render_dpi
        self.header_footer_threshold = header_footer_threshold
        self.similarity_threshold = similarity_threshold
        self.extract_svg = extract_svg
        # Use provided directory or fall back to settings (which is always absolute)
        self.image_storage_dir = image_storage_dir or settings.IMAGE_STORAGE_DIR
        os.makedirs(self.image_storage_dir, exist_ok=True)

    def extract_images(
        self, pdf_path: str, pdf_name: str | None = None
    ) -> list[dict[str, Any]]:
        """
        Extract raster images from a PDF file.

        Args:
            pdf_path: Path to the PDF file
            pdf_name: Name of the PDF (without extension) for naming saved images.
                     If None, uses the filename from pdf_path.

        Returns:
            List of dictionaries containing image metadata:
            [
                {
                    "file_path": str,
                    "page_number": int,
                    "image_index": int,
                    "width": int,
                    "height": int,
                    "position": {"x": float, "y": float},
                    "size_bytes": int,
                    "type": str,  # "raster" or "vector"
                },
                ...
            ]
        """
        if fitz is None:
            raise ImportError("PyMuPDF (fitz) is not installed")

        # Get PDF name from path if not provided
        if pdf_name is None:
            pdf_name = Path(pdf_path).stem

        extracted_images = []

        try:
            # Open PDF
            doc = fitz.open(pdf_path)
            logger.info(f"Extracting raster images from PDF: {pdf_path} ({len(doc)} pages)")

            # Extract raster images (embedded bitmaps)
            raster_images = self._extract_raster_images(doc, pdf_name)
            extracted_images.extend(raster_images)

            doc.close()
            logger.info(
                f"Image extraction completed: {len(extracted_images)} raster images extracted"
            )

        except Exception as e:
            logger.error(f"Error extracting images from PDF {pdf_path}: {e}")
            raise

        return extracted_images

    def _extract_raster_images(
        self, doc: "fitz.Document", pdf_name: str
    ) -> list[dict[str, Any]]:
        """Extract embedded raster images from PDF."""
        extracted_images = []
        image_idx = 0

        # Iterate through pages
        for page_num in range(len(doc)):
            page = doc[page_num]
            image_list = page.get_images()

            logger.debug(
                f"Page {page_num + 1}: Found {len(image_list)} raster image(s)"
            )

            # Extract each image
            for img_idx, img in enumerate(image_list):
                try:
                    # Get image data
                    xref = img[0]
                    base_image = doc.extract_image(xref)
                    image_bytes = base_image["image"]
                    image_ext = base_image["ext"]
                    width = base_image["width"]
                    height = base_image["height"]

                    # Filter out small images
                    if width < self.min_width or height < self.min_height:
                        logger.debug(
                            f"Page {page_num + 1}, Image {img_idx + 1}: "
                            f"Skipping small image ({width}x{height}px)"
                        )
                        continue

                    # Increment image index for valid images only
                    image_idx += 1

                    # Get image position on page (if available)
                    try:
                        image_rects = page.get_image_rects(xref)
                        if image_rects:
                            rect = image_rects[0]
                            position = {"x": float(rect.x0), "y": float(rect.y0)}
                        else:
                            position = {"x": 0.0, "y": 0.0}
                    except Exception:
                        position = {"x": 0.0, "y": 0.0}

                    # Generate filename
                    filename = f"{pdf_name}_page{page_num + 1}_img{image_idx}.png"
                    file_path = os.path.join(self.image_storage_dir, filename)

                    # Save image (always save as PNG for consistency)
                    with open(file_path, "wb") as img_file:
                        if image_ext.lower() != "png":
                            try:
                                from PIL import Image
                                import io

                                img_obj = Image.open(io.BytesIO(image_bytes))
                                if img_obj.mode == "RGBA":
                                    rgb_img = Image.new("RGB", img_obj.size, (255, 255, 255))
                                    rgb_img.paste(img_obj, mask=img_obj.split()[3])
                                    img_obj = rgb_img
                                img_obj.save(img_file, format="PNG")
                            except (ImportError, Exception) as e:
                                img_file.write(image_bytes)
                                logger.warning(
                                    f"Could not convert image to PNG ({e}), "
                                    f"saving as {image_ext}."
                                )
                        else:
                            img_file.write(image_bytes)

                    # Get file size
                    size_bytes = os.path.getsize(file_path)

                    # Create metadata
                    image_metadata = {
                        "file_path": file_path,
                        "page_number": page_num + 1,
                        "image_index": image_idx,
                        "width": width,
                        "height": height,
                        "position": position,
                        "size_bytes": size_bytes,
                        "original_format": image_ext,
                        "type": "raster",
                    }

                    extracted_images.append(image_metadata)

                    logger.info(
                        f"Extracted raster image: {filename} "
                        f"({width}x{height}px, {size_bytes} bytes) "
                        f"from page {page_num + 1}"
                    )

                except Exception as e:
                    logger.warning(
                        f"Failed to extract raster image {img_idx + 1} "
                        f"from page {page_num + 1}: {e}"
                    )
                    continue

        return extracted_images

    def filter_logos_and_icons(
        self, images: list[dict[str, Any]], max_aspect_ratio: float = 3.0
    ) -> list[dict[str, Any]]:
        """
        Filter out potential logos and icons based on aspect ratio.

        Logos and icons often have extreme aspect ratios (very wide or very tall).

        Args:
            images: List of image metadata dictionaries
            max_aspect_ratio: Maximum aspect ratio (width/height or height/width)
                             to consider an image valid (default: 3.0)

        Returns:
            Filtered list of images
        """
        filtered = []
        for img in images:
            width = img["width"]
            height = img["height"]
            aspect_ratio = max(width / height, height / width)

            if aspect_ratio <= max_aspect_ratio:
                filtered.append(img)
            else:
                logger.debug(
                    f"Filtered out potential logo/icon: "
                    f"{img.get('file_path', 'unknown')} "
                    f"(aspect ratio: {aspect_ratio:.2f})"
                )

        return filtered
