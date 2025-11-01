"""
PDF Processing Service for DocFlow.

Handles PDF splitting, page extraction, and conversion to images
for AI analysis.
"""

from typing import List, Tuple
from pathlib import Path
import io
from PIL import Image
import PyPDF2
import logging
import tempfile
import os

logger = logging.getLogger(__name__)


class PDFProcessor:
    """
    Processes PDF files for DocFlow.
    
    Handles:
    - Splitting multi-page PDFs into individual pages
    - Converting PDF pages to images
    - Validating PDF integrity
    """
    
    def __init__(self, temp_dir: str = None) -> None:
        """
        Initialize PDF processor.
        
        Args:
            temp_dir: Directory for temporary files (defaults to system temp)
        """
        self.temp_dir = temp_dir or tempfile.gettempdir()
        Path(self.temp_dir).mkdir(parents=True, exist_ok=True)
    
    async def validate_pdf(self, file_data: bytes) -> Tuple[bool, str]:
        """
        Validate that uploaded file is a valid PDF.
        
        Args:
            file_data: PDF file bytes
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        try:
            pdf_reader = PyPDF2.PdfReader(io.BytesIO(file_data))
            num_pages = len(pdf_reader.pages)
            
            if num_pages == 0:
                return False, "PDF has no pages"
            
            logger.info(f"PDF validation passed: {num_pages} pages")
            return True, ""
        
        except PyPDF2.PdfReadError as e:
            error_msg = f"Invalid PDF format: {str(e)}"
            logger.error(error_msg)
            return False, error_msg
        
        except Exception as e:
            error_msg = f"PDF validation error: {str(e)}"
            logger.error(error_msg)
            return False, error_msg
    
    async def get_page_count(self, file_data: bytes) -> int:
        """
        Get the number of pages in a PDF.
        
        Args:
            file_data: PDF file bytes
            
        Returns:
            Number of pages
            
        Raises:
            ValueError: If PDF is invalid
        """
        try:
            pdf_reader = PyPDF2.PdfReader(io.BytesIO(file_data))
            return len(pdf_reader.pages)
        except Exception as e:
            logger.error(f"Error reading page count: {e}")
            raise ValueError(f"Cannot read PDF: {str(e)}")
    
    async def split_pdf(self, file_data: bytes) -> List[bytes]:
        """
        Split a multi-page PDF into individual page PDFs.
        
        Creates a separate PDF file for each page of the input PDF.
        
        Args:
            file_data: PDF file bytes
            
        Returns:
            List of bytes objects, one per page
            
        Raises:
            ValueError: If PDF cannot be split
        """
        try:
            pdf_reader = PyPDF2.PdfReader(io.BytesIO(file_data))
            page_pdfs = []
            
            num_pages = len(pdf_reader.pages)
            logger.info(f"Splitting PDF into {num_pages} individual pages")
            
            for page_num in range(num_pages):
                # Create a new PDF with just this page
                pdf_writer = PyPDF2.PdfWriter()
                pdf_writer.add_page(pdf_reader.pages[page_num])
                
                # Write to bytes
                output = io.BytesIO()
                pdf_writer.write(output)
                output.seek(0)
                page_pdfs.append(output.getvalue())
                
                logger.debug(f"Extracted page {page_num + 1}")
            
            logger.info(f"Successfully split PDF into {len(page_pdfs)} pages")
            return page_pdfs
        
        except Exception as e:
            error_msg = f"Error splitting PDF: {str(e)}"
            logger.error(error_msg)
            raise ValueError(error_msg)
    
    async def pdf_page_to_image(
        self,
        pdf_bytes: bytes,
        dpi: int = 200
    ) -> bytes:
        """
        Convert a single-page PDF to an image.
        
        Converts PDF to PNG image at specified DPI for better OCR results.
        
        Args:
            pdf_bytes: PDF file bytes (should be single page)
            dpi: Resolution for conversion (higher = better quality, slower)
            
        Returns:
            PNG image bytes
            
        Raises:
            ValueError: If conversion fails
        """
        try:
            from pdf2image import convert_from_bytes
            
            logger.debug(f"Converting PDF page to image at {dpi} DPI")
            
            # Convert PDF page to image
            images = convert_from_bytes(
                pdf_bytes,
                dpi=dpi,
                first_page=1,
                last_page=1,
            )
            
            if not images:
                raise ValueError("No images generated from PDF")
            
            image = images[0]
            
            # Convert to PNG bytes
            png_bytes = io.BytesIO()
            image.save(png_bytes, format='PNG')
            png_bytes.seek(0)
            
            logger.debug("Successfully converted PDF page to image")
            return png_bytes.getvalue()
        
        except ImportError:
            error_msg = "pdf2image not installed. Install with: pip install pdf2image"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        except Exception as e:
            error_msg = f"Error converting PDF to image: {str(e)}"
            logger.error(error_msg)
            raise ValueError(error_msg)
    
    async def process_pdf_for_analysis(
        self,
        file_data: bytes
    ) -> List[Tuple[int, bytes]]:
        """
        Process a PDF file for AI analysis.
        
        Splits PDF into pages and converts each to an image.
        
        Args:
            file_data: PDF file bytes
            
        Returns:
            List of (page_number, image_bytes) tuples
            
        Raises:
            ValueError: If processing fails
        """
        try:
            # Validate PDF
            is_valid, error = await self.validate_pdf(file_data)
            if not is_valid:
                raise ValueError(error)
            
            # Split into individual pages
            page_pdfs = await self.split_pdf(file_data)
            
            # Convert each page to image
            page_images = []
            for page_num, page_pdf in enumerate(page_pdfs, start=1):
                try:
                    image_bytes = await self.pdf_page_to_image(page_pdf)
                    page_images.append((page_num, image_bytes))
                    logger.debug(f"Page {page_num} converted successfully")
                except Exception as e:
                    logger.error(f"Error processing page {page_num}: {e}")
                    # Continue with other pages rather than failing completely
            
            if not page_images:
                raise ValueError("No pages were successfully converted")
            
            logger.info(f"Successfully processed {len(page_images)} pages for analysis")
            return page_images
        
        except ValueError:
            raise
        except Exception as e:
            error_msg = f"Unexpected error processing PDF: {str(e)}"
            logger.error(error_msg)
            raise ValueError(error_msg)
