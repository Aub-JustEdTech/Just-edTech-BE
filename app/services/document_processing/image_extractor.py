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

try:
    import numpy as np
    from PIL import Image
except ImportError:
    np = None
    Image = None

try:
    import cv2
except ImportError:
    cv2 = None
    # Note: OpenCV (opencv-python) is optional but recommended for better
    # contour detection. Install with: pip install opencv-python

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

    def _extract_vector_graphics(
        self, doc: "fitz.Document", pdf_name: str
    ) -> list[dict[str, Any]]:
        """Extract vector graphics by rendering pages and detecting non-repetitive regions.
        
        OPTIMIZED: Process one page at a time to minimize memory usage.
        """
        if np is None or Image is None:
            logger.warning(
                "NumPy and Pillow are required for vector graphics extraction. "
                "Skipping vector extraction."
            )
            return []

        extracted_images = []
        
        try:
            # First pass: render pages one at a time to detect repetitive regions
            # Store only metadata, not full images
            page_metadata = []
            for page_num in range(len(doc)):
                page = doc[page_num]
                mat = fitz.Matrix(self.render_dpi / 72, self.render_dpi / 72)
                pix = page.get_pixmap(matrix=mat)
                page_metadata.append((page_num, pix.width, pix.height))
                pix = None

            if not page_metadata:
                return []

            # Detect repetitive regions using only metadata (simplified)
            # For memory efficiency, we skip cross-page comparison and use fixed header/footer zones
            repetitive_regions = self._detect_repetitive_regions_simple(page_metadata)

            # Second pass: process one page at a time (memory-efficient)
            image_idx = 0
            
            for page_num, width, height in page_metadata:
                # Render only this page
                page = doc[page_num]
                mat = fitz.Matrix(self.render_dpi / 72, self.render_dpi / 72)
                pix = page.get_pixmap(matrix=mat)
                img_data = pix.tobytes("png")
                img = Image.open(io.BytesIO(img_data))
                pix = None
                
                # Convert to numpy array for processing
                img_array = np.array(img)
                img = None
                
                # Extract regions of interest (excluding repetitive areas)
                regions = self._extract_content_regions(
                    img_array, page_num, repetitive_regions, width, height
                )
                img_array = None

                # Save each extracted region
                for region_idx, (region_img, bbox) in enumerate(regions):
                    region_width = bbox[2] - bbox[0]
                    region_height = bbox[3] - bbox[1]

                    # Filter out small regions (use reduced thresholds for vectors)
                    min_w = max(50, self.min_width // 2)
                    min_h = max(50, self.min_height // 2)
                    if region_width < min_w or region_height < min_h:
                        continue

                    image_idx += 1
                    filename = f"{pdf_name}_page{page_num + 1}_vector{image_idx}.png"
                    file_path = os.path.join(self.image_storage_dir, filename)

                    # Save region
                    region_pil = Image.fromarray(region_img)
                    region_pil.save(file_path, format="PNG")
                    size_bytes = os.path.getsize(file_path)

                    image_metadata = {
                        "file_path": file_path,
                        "page_number": page_num + 1,
                        "image_index": image_idx,
                        "width": region_width,
                        "height": region_height,
                        "position": {"x": float(bbox[0]), "y": float(bbox[1])},
                        "size_bytes": size_bytes,
                        "original_format": "png",
                        "type": "vector",
                    }

                    extracted_images.append(image_metadata)
                    logger.info(
                        f"Extracted vector region: {filename} "
                        f"({region_width}x{region_height}px) from page {page_num + 1}"
                    )
                
                regions = None

        except Exception as e:
            logger.warning(f"Error extracting vector graphics: {e}", exc_info=True)
            # Don't fail completely if vector extraction fails

        return extracted_images

    def _detect_repetitive_regions_simple(
        self, page_metadata: list[tuple[int, int, int]]
    ) -> dict[str, Any]:
        """
        Detect repetitive regions (headers, footers) using simple heuristics.
        Memory-efficient version that doesn't require loading all pages.
        
        Returns a dictionary with excluded regions.
        """
        if not page_metadata:
            return {"headers": [], "footers": [], "logos": []}

        repetitive_regions = {"headers": [], "footers": [], "logos": []}

        try:
            # Get page dimensions from first page (assuming consistent sizing)
            _, page_width, page_height = page_metadata[0]
            
            # Define header and footer regions based on threshold
            header_height = int(page_height * self.header_footer_threshold)
            footer_height = int(page_height * self.header_footer_threshold)
            
            # Header region: top portion of page
            repetitive_regions["headers"].append({
                "x0": 0,
                "y0": 0,
                "x1": page_width,
                "y1": header_height,
            })
            
            # Footer region: bottom portion of page
            repetitive_regions["footers"].append({
                "x0": 0,
                "y0": page_height - footer_height,
                "x1": page_width,
                "y1": page_height,
            })

        except Exception as e:
            logger.warning(f"Error detecting repetitive regions: {e}")

        return repetitive_regions

    def _detect_repetitive_regions(
        self, page_renders: list[tuple[int, Image.Image, int, int]]
    ) -> dict[str, Any]:
        """
        Detect repetitive regions (headers, footers, logos) across pages.
        
        DEPRECATED: This method loads all pages into memory. Use _detect_repetitive_regions_simple instead.
        
        Returns a dictionary with excluded regions.
        """
        if len(page_renders) < 2:
            # Need at least 2 pages to detect repetition
            return {"headers": [], "footers": [], "logos": []}

        repetitive_regions = {"headers": [], "footers": [], "logos": []}

        try:
            # Get page dimensions (assuming all pages have same size)
            _, first_img, page_width, page_height = page_renders[0]
            
            # Define header and footer regions based on threshold
            header_height = int(page_height * self.header_footer_threshold)
            footer_height = int(page_height * self.header_footer_threshold)
            
            # Header region: top portion of page
            repetitive_regions["headers"].append({
                "x0": 0,
                "y0": 0,
                "x1": page_width,
                "y1": header_height,
            })
            
            # Footer region: bottom portion of page
            repetitive_regions["footers"].append({
                "x0": 0,
                "y0": page_height - footer_height,
                "x1": page_width,
                "y1": page_height,
            })

            # Detect logos by comparing small regions across pages
            if np is not None and len(page_renders) >= 3:
                # Sample regions from first page and compare with others
                sample_regions = [
                    (0, 0, min(200, page_width // 4), min(200, page_height // 4)),  # Top-left
                    (page_width - min(200, page_width // 4), 0, page_width, min(200, page_height // 4)),  # Top-right
                ]

                for x0, y0, x1, y1 in sample_regions:
                    first_region = np.array(first_img.crop((x0, y0, x1, y1)))
                    similar_count = 0

                    for page_num, img, _, _ in page_renders[1:]:
                        region = np.array(img.crop((x0, y0, x1, y1)))
                        similarity = self._calculate_similarity(first_region, region)
                        if similarity >= self.similarity_threshold:
                            similar_count += 1

                    # If region appears on most pages, it's likely a logo
                    if similar_count >= len(page_renders) * 0.7:  # 70% of pages
                        repetitive_regions["logos"].append({
                            "x0": x0,
                            "y0": y0,
                            "x1": x1,
                            "y1": y1,
                        })

        except Exception as e:
            logger.warning(f"Error detecting repetitive regions: {e}")

        return repetitive_regions

    def _calculate_similarity(self, img1: np.ndarray, img2: np.ndarray) -> float:
        """Calculate similarity between two image arrays (0-1)."""
        if img1.shape != img2.shape:
            # Resize if needed
            try:
                img1_pil = Image.fromarray(img1)
                img2_pil = Image.fromarray(img2)
                img2_pil = img2_pil.resize(img1_pil.size)
                img2 = np.array(img2_pil)
            except Exception:
                return 0.0

        # Normalize and calculate structural similarity
        try:
            # Simple mean squared error approach
            diff = img1.astype(float) - img2.astype(float)
            mse = np.mean(diff ** 2)
            # Convert MSE to similarity (0-1 scale)
            max_mse = 255.0 ** 2
            similarity = 1.0 - min(mse / max_mse, 1.0)
            return similarity
        except Exception:
            return 0.0

    def _extract_content_regions(
        self,
        img_array: np.ndarray,
        page_num: int,
        repetitive_regions: dict[str, Any],
        page_width: int,
        page_height: int,
    ) -> list[tuple[np.ndarray, tuple[int, int, int, int]]]:
        """
        Extract content regions from page, excluding repetitive elements.
        
        Returns list of (image_array, bbox) tuples.
        """
        regions = []

        try:
            # Get excluded regions
            excluded_rects = []
            for region_type in ["headers", "footers", "logos"]:
                excluded_rects.extend(repetitive_regions.get(region_type, []))

            # Calculate content area (excluding headers/footers)
            header_height = int(page_height * self.header_footer_threshold)
            footer_height = int(page_height * self.header_footer_threshold)
            
            content_y0 = header_height
            content_y1 = page_height - footer_height

            if content_y1 <= content_y0:
                return regions

            # Extract main content region (excluding headers/footers)
            content_region = img_array[content_y0:content_y1, :]
            
            # Check if content region has significant visual content
            if not self._has_significant_content(content_region):
                return regions

            # Detect and extract individual illustrations
            illustration_regions = self._detect_illustrations(
                content_region, content_y0, excluded_rects
            )
            
            if illustration_regions:
                # Use detected individual illustrations
                logger.debug(
                    f"Page {page_num + 1}: Detected {len(illustration_regions)} illustration regions"
                )
                regions.extend(illustration_regions)
            else:
                # Fallback: if no individual illustrations detected,
                # try white-space based splitting
                logger.debug(
                    f"Page {page_num + 1}: No illustrations detected, trying whitespace splitting"
                )
                split_regions = self._split_by_whitespace(
                    content_region, content_y0, page_width
                )
                if split_regions:
                    logger.debug(
                        f"Page {page_num + 1}: Split into {len(split_regions)} regions by whitespace"
                    )
                    regions.extend(split_regions)
                else:
                    # Last resort: return full content region if nothing else works
                    # But only if it has significant content
                    logger.debug(
                        f"Page {page_num + 1}: No regions detected, checking full content region"
                    )
                    if self._has_significant_content(content_region, threshold=0.02):
                        logger.debug(
                            f"Page {page_num + 1}: Full content region has content, using it"
                        )
                        bbox = (0, content_y0, page_width, content_y1)
                        regions.append((content_region, bbox))
                    else:
                        logger.debug(
                            f"Page {page_num + 1}: Full content region appears empty, skipping"
                        )

        except Exception as e:
            logger.warning(f"Error extracting content regions from page {page_num + 1}: {e}")

        return regions

    def _has_significant_content(self, img_array: np.ndarray, threshold: float = 0.03) -> bool:
        """Check if image region has significant non-white content.
        
        Lowered threshold from 0.05 to 0.03 to catch more subtle vector graphics.
        Also lowered white threshold from 250 to 245 to catch light gray vectors.
        """
        if np is None:
            return True  # Assume content if we can't check
        
        try:
            # Convert to grayscale if needed
            if len(img_array.shape) == 3:
                gray = np.mean(img_array, axis=2)
            else:
                gray = img_array
            
            # Calculate percentage of non-white pixels
            # Lowered threshold from 250 to 245 to catch light gray vectors
            non_white = np.sum(gray < 245) / gray.size
            return non_white > threshold
        except Exception:
            return True

    def _detect_illustrations(
        self,
        content_region: np.ndarray,
        y_offset: int,
        excluded_rects: list[dict[str, Any]],
    ) -> list[tuple[np.ndarray, tuple[int, int, int, int]]]:
        """
        Detect individual illustrations using hybrid approach:
        1. Edge detection to find boundaries
        2. Connected component analysis to find bounded regions
        3. White space analysis to refine and separate illustrations
        """
        if np is None:
            return []

        regions = []
        
        try:
            height, width = content_region.shape[:2]
            
            # Convert to grayscale if needed
            if len(content_region.shape) == 3:
                gray = np.mean(content_region, axis=2).astype(np.uint8)
            else:
                gray = content_region.astype(np.uint8)

            # Use OpenCV if available for better contour detection
            if cv2 is not None:
                illustrations = self._detect_with_opencv(
                    gray, content_region, y_offset, excluded_rects
                )
                if illustrations:
                    return illustrations

            # Fallback to numpy-based detection
            illustrations = self._detect_with_numpy(
                gray, content_region, y_offset, excluded_rects
            )
            regions.extend(illustrations)
                
        except Exception as e:
            logger.warning(f"Error detecting illustrations: {e}", exc_info=True)

        return regions

    def _detect_with_opencv(
        self,
        gray: np.ndarray,
        content_region: np.ndarray,
        y_offset: int,
        excluded_rects: list[dict[str, Any]],
    ) -> list[tuple[np.ndarray, tuple[int, int, int, int]]]:
        """Detect illustrations using OpenCV contour detection.
        
        Improved to catch more vectors by using adaptive thresholds and lower size requirements.
        """
        regions = []
        
        try:
            # Use adaptive thresholding to catch subtle edges
            # First try with lower thresholds for subtle vectors
            edges1 = cv2.Canny(gray, 30, 100)
            # Also try with even lower thresholds
            edges2 = cv2.Canny(gray, 20, 80)
            # Combine both edge detections
            edges = cv2.bitwise_or(edges1, edges2)
            
            # Dilate edges to connect nearby lines (increased iterations for better connectivity)
            kernel = np.ones((3, 3), np.uint8)
            dilated = cv2.dilate(edges, kernel, iterations=3)
            
            # Find contours - use RETR_TREE to catch nested contours too
            contours, _ = cv2.findContours(
                dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            
            # Get bounding boxes for each contour
            # Reduced minimum size requirements to catch smaller vectors
            min_w = max(50, self.min_width // 2)
            min_h = max(50, self.min_height // 2)
            bounding_boxes = []
            for contour in contours:
                x, y, w, h = cv2.boundingRect(contour)
                # Use reduced minimum size
                if w >= min_w and h >= min_h:
                    bounding_boxes.append((x, y, x + w, y + h))
            
            # Merge overlapping boxes and refine with white space
            merged_boxes = self._merge_and_refine_boxes(
                bounding_boxes, gray, content_region.shape[:2]
            )
            
            # Extract regions
            for x0, y0, x1, y1 in merged_boxes:
                # Check if region overlaps with excluded areas
                if self._overlaps_excluded(x0, y0, x1, y1, excluded_rects):
                    continue
                
                # Extract region with padding
                padding = 5
                x0_pad = max(0, x0 - padding)
                y0_pad = max(0, y0 - padding)
                x1_pad = min(content_region.shape[1], x1 + padding)
                y1_pad = min(content_region.shape[0], y1 + padding)
                
                region = content_region[y0_pad:y1_pad, x0_pad:x1_pad]
                
                if self._has_significant_content(region):
                    bbox = (x0_pad, y_offset + y0_pad, x1_pad, y_offset + y1_pad)
                    regions.append((region, bbox))
                    
        except Exception as e:
            logger.debug(f"OpenCV detection failed: {e}")

        return regions

    def _detect_with_numpy(
        self,
        gray: np.ndarray,
        content_region: np.ndarray,
        y_offset: int,
        excluded_rects: list[dict[str, Any]],
    ) -> list[tuple[np.ndarray, tuple[int, int, int, int]]]:
        """Detect illustrations using numpy-based edge detection and connected components."""
        regions = []
        
        try:
            height, width = gray.shape
            
            # Edge detection using Sobel filters
            sobel_x = np.abs(np.gradient(gray.astype(float), axis=1))
            sobel_y = np.abs(np.gradient(gray.astype(float), axis=0))
            edges = np.sqrt(sobel_x ** 2 + sobel_y ** 2)
            
            # Lower percentile threshold to catch more subtle edges
            # Changed from 85th percentile to 75th percentile
            edge_threshold = np.percentile(edges, 75)  # Top 25% of edge strengths
            binary_edges = (edges > edge_threshold).astype(np.uint8) * 255
            
            # Find connected components (bounded regions)
            # Use a simple flood-fill approach to find regions
            bounding_boxes = self._find_connected_regions(binary_edges, gray)
            
            # Refine boxes using white space analysis
            refined_boxes = self._refine_boxes_with_whitespace(
                bounding_boxes, gray, content_region
            )
            
            # Extract regions
            for x0, y0, x1, y1 in refined_boxes:
                # Check if region overlaps with excluded areas
                if self._overlaps_excluded(x0, y0, x1, y1, excluded_rects):
                    continue
                
                # Extract region with small padding
                padding = 5
                x0_pad = max(0, x0 - padding)
                y0_pad = max(0, y0 - padding)
                x1_pad = min(width, x1 + padding)
                y1_pad = min(height, y1 + padding)
                
                region = content_region[y0_pad:y1_pad, x0_pad:x1_pad]
                
                if self._has_significant_content(region):
                    bbox = (x0_pad, y_offset + y0_pad, x1_pad, y_offset + y1_pad)
                    regions.append((region, bbox))
                    
        except Exception as e:
            logger.debug(f"Numpy detection failed: {e}")

        return regions

    def _find_connected_regions(
        self, binary_edges: np.ndarray, gray: np.ndarray
    ) -> list[tuple[int, int, int, int]]:
        """
        Find connected regions using flood fill approach.
        
        Uses sampling for performance on large images.
        Improved to catch more vectors by using lower thresholds and finer sampling.
        """
        boxes = []
        height, width = binary_edges.shape
        visited = np.zeros_like(binary_edges, dtype=bool)
        
        # Lower threshold for non-white content to catch light gray vectors
        # Changed from 240 to 245 to be more inclusive
        content_threshold = 245
        
        # Finer sampling grid - reduced step size to catch smaller regions
        # Changed from max(10, min(30, width // 50, height // 50)) to max(5, min(20, width // 100, height // 100))
        step = max(5, min(20, width // 100, height // 100))
        
        for y in range(0, height, step):
            for x in range(0, width, step):
                if visited[y, x] or gray[y, x] > content_threshold:
                    continue
                
                # Flood fill to find connected region
                region_pixels = self._flood_fill(gray, x, y, content_threshold, visited)
                
                # Reduced minimum pixels requirement - changed from // 4 to // 8
                # This allows smaller but valid vector graphics to be detected
                min_pixels = (self.min_width * self.min_height) // 8
                if len(region_pixels) < min_pixels:
                    continue
                
                # Get bounding box
                xs = [p[0] for p in region_pixels]
                ys = [p[1] for p in region_pixels]
                
                x0, x1 = min(xs), max(xs) + 1
                y0, y1 = min(ys), max(ys) + 1
                
                # Check minimum size (reduced thresholds for smaller valid vectors)
                # Allow regions that are at least 50% of minimum size
                min_w = max(50, self.min_width // 2)
                min_h = max(50, self.min_height // 2)
                if (x1 - x0) >= min_w and (y1 - y0) >= min_h:
                    boxes.append((x0, y0, x1, y1))
        
        return boxes

    def _flood_fill(
        self,
        img: np.ndarray,
        start_x: int,
        start_y: int,
        threshold: int,
        visited: np.ndarray,
    ) -> list[tuple[int, int]]:
        """Simple flood fill to find connected region."""
        height, width = img.shape
        pixels = []
        stack = [(start_x, start_y)]
        
        while stack:
            x, y = stack.pop()
            
            if x < 0 or x >= width or y < 0 or y >= height:
                continue
            if visited[y, x] or img[y, x] > threshold:
                continue
            
            visited[y, x] = True
            pixels.append((x, y))
            
            # Add neighbors
            stack.append((x + 1, y))
            stack.append((x - 1, y))
            stack.append((x, y + 1))
            stack.append((x, y - 1))
        
        return pixels

    def _refine_boxes_with_whitespace(
        self,
        boxes: list[tuple[int, int, int, int]],
        gray: np.ndarray,
        content_region: np.ndarray,
    ) -> list[tuple[int, int, int, int]]:
        """Refine bounding boxes using white space analysis."""
        if not boxes:
            return []
        
        refined = []
        height, width = gray.shape
        white_threshold = 250
        
        for x0, y0, x1, y1 in boxes:
            # Expand box to include nearby content, but stop at white space
            # Check horizontal expansion
            while x0 > 0:
                col = gray[y0:y1, x0 - 1]
                if np.mean(col) < white_threshold:
                    x0 -= 1
                else:
                    break
            
            while x1 < width:
                col = gray[y0:y1, x1]
                if np.mean(col) < white_threshold:
                    x1 += 1
                else:
                    break
            
            # Check vertical expansion
            while y0 > 0:
                row = gray[y0 - 1, x0:x1]
                if np.mean(row) < white_threshold:
                    y0 -= 1
                else:
                    break
            
            while y1 < height:
                row = gray[y1, x0:x1]
                if np.mean(row) < white_threshold:
                    y1 += 1
                else:
                    break
            
            # Ensure minimum size
            if (x1 - x0) >= self.min_width and (y1 - y0) >= self.min_height:
                refined.append((x0, y0, x1, y1))
        
        return refined

    def _merge_and_refine_boxes(
        self,
        boxes: list[tuple[int, int, int, int]],
        gray: np.ndarray,
        shape: tuple[int, int],
    ) -> list[tuple[int, int, int, int]]:
        """Merge overlapping boxes and refine boundaries."""
        if not boxes:
            return []
        
        # Sort by area (largest first)
        boxes = sorted(boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]), reverse=True)
        
        merged = []
        for box in boxes:
            x0, y0, x1, y1 = box
            
            # Check if this box significantly overlaps with any merged box
            overlaps = False
            for mx0, my0, mx1, my1 in merged:
                overlap_x = max(0, min(x1, mx1) - max(x0, mx0))
                overlap_y = max(0, min(y1, my1) - max(y0, my0))
                overlap_area = overlap_x * overlap_y
                box_area = (x1 - x0) * (y1 - y0)
                
                if overlap_area > box_area * 0.5:  # 50% overlap
                    overlaps = True
                    break
            
            if not overlaps:
                # Refine box using white space
                refined = self._refine_box_with_whitespace((x0, y0, x1, y1), gray)
                if refined:
                    merged.append(refined)
        
        return merged

    def _refine_box_with_whitespace(
        self, box: tuple[int, int, int, int], gray: np.ndarray
    ) -> tuple[int, int, int, int] | None:
        """Refine a single box by expanding to white space boundaries."""
        x0, y0, x1, y1 = box
        height, width = gray.shape
        white_threshold = 250
        
        # Expand horizontally
        while x0 > 0 and np.mean(gray[y0:y1, max(0, x0 - 5):x0]) < white_threshold:
            x0 -= 1
        while x1 < width and np.mean(gray[y0:y1, x1:min(width, x1 + 5)]) < white_threshold:
            x1 += 1
        
        # Expand vertically
        while y0 > 0 and np.mean(gray[max(0, y0 - 5):y0, x0:x1]) < white_threshold:
            y0 -= 1
        while y1 < height and np.mean(gray[y1:min(height, y1 + 5), x0:x1]) < white_threshold:
            y1 += 1
        
        if (x1 - x0) >= self.min_width and (y1 - y0) >= self.min_height:
            return (x0, y0, x1, y1)
        return None

    def _overlaps_excluded(
        self,
        x0: int,
        y0: int,
        x1: int,
        y1: int,
        excluded_rects: list[dict[str, Any]],
    ) -> bool:
        """Check if box overlaps with excluded regions."""
        for rect in excluded_rects:
            ex0 = rect.get("x0", 0)
            ey0 = rect.get("y0", 0)
            ex1 = rect.get("x1", 0)
            ey1 = rect.get("y1", 0)
            
            # Check for overlap
            if not (x1 <= ex0 or x0 >= ex1 or y1 <= ey0 or y0 >= ey1):
                return True
        return False

    def _split_by_whitespace(
        self,
        content_region: np.ndarray,
        y_offset: int,
        page_width: int,
        min_gap: int = 50,
    ) -> list[tuple[np.ndarray, tuple[int, int, int, int]]]:
        """Split content region by horizontal white space gaps."""
        if np is None:
            return []
        
        regions = []
        
        try:
            # Convert to grayscale
            if len(content_region.shape) == 3:
                gray = np.mean(content_region, axis=2)
            else:
                gray = content_region
            
            height, width = gray.shape
            
            # Find rows that are mostly white (potential split points)
            white_threshold = 250
            row_white_ratio = np.sum(gray > white_threshold, axis=1) / width
            
            # Find consecutive white rows (gaps)
            split_points = []
            in_gap = False
            gap_start = 0
            
            for y, ratio in enumerate(row_white_ratio):
                if ratio > 0.9:  # Mostly white row
                    if not in_gap:
                        in_gap = True
                        gap_start = y
                else:
                    if in_gap and (y - gap_start) >= min_gap:
                        # Found a significant gap, use the middle as split point
                        split_points.append((gap_start + y) // 2)
                    in_gap = False
            
            # Extract regions between split points
            current_y = 0
            for split_y in split_points:
                if split_y > current_y + self.min_height:
                    region = content_region[current_y:split_y, :]
                    if self._has_significant_content(region):
                        bbox = (0, y_offset + current_y, page_width, y_offset + split_y)
                        regions.append((region, bbox))
                current_y = split_y
            
            # Add final region
            if current_y < height - self.min_height:
                region = content_region[current_y:, :]
                if self._has_significant_content(region):
                    bbox = (0, y_offset + current_y, page_width, y_offset + height)
                    regions.append((region, bbox))
            
        except Exception as e:
            logger.debug(f"Error splitting by whitespace: {e}")

        return regions

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

    def _extract_svg_graphics(
        self, doc: "fitz.Document", pdf_name: str
    ) -> list[dict[str, Any]]:
        """
        Extract vector graphics as SVG files (preserves vector format).
        
        This method extracts pages as SVG, which preserves vector graphics
        in their native scalable format. Useful when you need to preserve
        vector quality or edit graphics.
        
        Args:
            doc: PyMuPDF document object
            pdf_name: Name prefix for saved files
            
        Returns:
            List of image metadata dictionaries
        """
        extracted_images = []
        
        try:
            for page_num in range(len(doc)):
                page = doc[page_num]
                
                try:
                    # Get SVG representation of the page
                    svg_content = page.get_svg_image()
                    
                    if not svg_content or len(svg_content.strip()) == 0:
                        logger.debug(f"Page {page_num + 1}: No SVG content")
                        continue
                    
                    # Save SVG file
                    filename = f"{pdf_name}_page{page_num + 1}_vectors.svg"
                    file_path = os.path.join(self.image_storage_dir, filename)
                    
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write(svg_content)
                    
                    # Get page dimensions for metadata
                    page_rect = page.rect
                    width = int(page_rect.width)
                    height = int(page_rect.height)
                    size_bytes = os.path.getsize(file_path)
                    
                    image_metadata = {
                        "file_path": file_path,
                        "page_number": page_num + 1,
                        "image_index": len(extracted_images) + 1,
                        "width": width,
                        "height": height,
                        "position": {"x": 0.0, "y": 0.0},
                        "size_bytes": size_bytes,
                        "original_format": "svg",
                        "type": "vector_svg",
                    }
                    
                    extracted_images.append(image_metadata)
                    logger.info(
                        f"Extracted SVG: {filename} ({width}x{height}px, {size_bytes} bytes) "
                        f"from page {page_num + 1}"
                    )
                    
                except Exception as e:
                    logger.warning(
                        f"Failed to extract SVG from page {page_num + 1}: {e}"
                    )
                    continue
                    
        except Exception as e:
            logger.warning(f"Error extracting SVG graphics: {e}", exc_info=True)
        
        return extracted_images

    def extract_specific_regions(
        self,
        pdf_path: str,
        page_num: int,
        regions: list[tuple[float, float, float, float]],
        pdf_name: str | None = None,
        scale_factor: float = 3.0,
    ) -> list[dict[str, Any]]:
        """
        Extract specific rectangular regions from a PDF page.
        
        Useful for isolating diagrams, charts, or specific graphics when
        you know the exact coordinates.
        
        Args:
            pdf_path: Path to the PDF file
            page_num: Zero-based page number
            regions: List of (x0, y0, x1, y1) tuples defining crop areas in points
            pdf_name: Name prefix for saved files (defaults to PDF filename)
            scale_factor: Scaling factor for rendering (default: 3.0 for high quality)
            
        Returns:
            List of dictionaries containing extracted region metadata:
            [
                {
                    "file_path": str,
                    "page_number": int,
                    "region_index": int,
                    "width": int,
                    "height": int,
                    "bbox": {"x0": float, "y0": float, "x1": float, "y1": float},
                    "size_bytes": int,
                    "type": "region",
                },
                ...
            ]
        """
        if fitz is None:
            raise ImportError("PyMuPDF (fitz) is not installed")
        
        if pdf_name is None:
            pdf_name = Path(pdf_path).stem
        
        extracted_regions = []
        
        try:
            doc = fitz.open(pdf_path)
            
            if page_num >= len(doc) or page_num < 0:
                raise ValueError(f"Page number {page_num} out of range (0-{len(doc)-1})")
            
            page = doc[page_num]
            
            # Create matrix for rendering
            mat = fitz.Matrix(scale_factor, scale_factor)
            
            for region_idx, rect_coords in enumerate(regions):
                try:
                    x0, y0, x1, y1 = rect_coords
                    
                    # Create rectangle
                    clip_rect = fitz.Rect(x0, y0, x1, y1)
                    
                    # Render only this region at specified resolution
                    pix = page.get_pixmap(matrix=mat, clip=clip_rect)
                    
                    # Generate filename
                    filename = f"{pdf_name}_page{page_num + 1}_region{region_idx + 1}.png"
                    file_path = os.path.join(self.image_storage_dir, filename)
                    
                    # Save image
                    pix.save(file_path)
                    size_bytes = os.path.getsize(file_path)
                    
                    # Create metadata
                    region_metadata = {
                        "file_path": file_path,
                        "page_number": page_num + 1,
                        "region_index": region_idx + 1,
                        "width": pix.width,
                        "height": pix.height,
                        "bbox": {
                            "x0": float(x0),
                            "y0": float(y0),
                            "x1": float(x1),
                            "y1": float(y1),
                        },
                        "size_bytes": size_bytes,
                        "type": "region",
                    }
                    
                    extracted_regions.append(region_metadata)
                    logger.info(
                        f"Extracted region {region_idx + 1} from page {page_num + 1}: "
                        f"{filename} ({pix.width}x{pix.height}px)"
                    )
                    
                    pix = None  # Free memory
                    
                except Exception as e:
                    logger.warning(
                        f"Failed to extract region {region_idx + 1} from page {page_num + 1}: {e}"
                    )
                    continue
            
            doc.close()
            
        except Exception as e:
            logger.error(f"Error extracting specific regions from PDF: {e}")
            raise
        
        return extracted_regions

    def get_drawings_info(
        self, pdf_path: str, page_num: int | None = None
    ) -> dict[str, Any]:
        """
        Get information about vector drawings/paths in PDF pages.
        
        This method extracts drawing information (paths, lines, curves) which
        can be useful for analyzing vector graphics structure or debugging.
        
        Args:
            pdf_path: Path to the PDF file
            page_num: Specific page number (0-based). If None, analyzes all pages.
            
        Returns:
            Dictionary with drawing information:
            {
                "total_pages": int,
                "pages": [
                    {
                        "page_number": int,
                        "drawing_count": int,
                        "drawings": [
                            {
                                "type": str,  # "f", "s", "l", "c", etc.
                                "rect": {"x0": float, "y0": float, "x1": float, "y1": float},
                                "items": int,  # Number of drawing items
                            },
                            ...
                        ],
                    },
                    ...
                ],
            }
        """
        if fitz is None:
            raise ImportError("PyMuPDF (fitz) is not installed")
        
        result = {
            "total_pages": 0,
            "pages": [],
        }
        
        try:
            doc = fitz.open(pdf_path)
            result["total_pages"] = len(doc)
            
            pages_to_process = [page_num] if page_num is not None else range(len(doc))
            
            for pnum in pages_to_process:
                if pnum < 0 or pnum >= len(doc):
                    continue
                
                page = doc[pnum]
                
                try:
                    drawings = page.get_drawings()
                    
                    page_info = {
                        "page_number": pnum + 1,
                        "drawing_count": len(drawings),
                        "drawings": [],
                    }
                    
                    for drawing in drawings:
                        rect = drawing.get("rect", fitz.Rect(0, 0, 0, 0))
                        drawing_info = {
                            "type": drawing.get("type", "unknown"),
                            "rect": {
                                "x0": float(rect.x0),
                                "y0": float(rect.y0),
                                "x1": float(rect.x1),
                                "y1": float(rect.y1),
                            },
                            "items": len(drawing.get("items", [])),
                        }
                        page_info["drawings"].append(drawing_info)
                    
                    result["pages"].append(page_info)
                    
                except Exception as e:
                    logger.warning(
                        f"Failed to get drawings from page {pnum + 1}: {e}"
                    )
                    continue
            
            doc.close()
            
        except Exception as e:
            logger.error(f"Error getting drawings info from PDF: {e}")
            raise
        
        return result
