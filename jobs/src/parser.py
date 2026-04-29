import pymupdf # type: ignore
import pymupdf4llm # type: ignore
from markitdown import MarkItDown
from typing import Tuple
from io import BytesIO
import logging
import uuid
from pathlib import Path
from PIL import Image # type: ignore

md = MarkItDown()

from src.s3_service import s3_service

logger = logging.getLogger(__name__)


def sanitize_string(text: str) -> str:
    """
    Remove NULL bytes and other problematic characters from strings before saving to database
    """
    if text is None:
        return ""

    # Remove NULL bytes
    return text.replace("\x00", "")


def extract_text_from_document(file_path: str) -> str:
    """
    Extract text content from a supported document file.
    """

    def is_valid_text(text: str) -> bool:
        """
        Check if the extracted text is valid (not empty or whitespace).
        """
        return bool(
            text
            and text.strip()
            and text.split("\n") != [""]
            and text.split(" ") != [""]
        )

    ext = Path(file_path).suffix.lower()

    try:
        md_text = md.convert(file_path).markdown
        md_text = sanitize_string(md_text)
        if not is_valid_text(md_text) and ext == ".pdf":
            # Fallback to pymupdf4llm if MarkItDown fails for PDFs
            md_text = pymupdf4llm.to_markdown(file_path)
            md_text = sanitize_string(md_text)

        if not is_valid_text(md_text):
            # If both methods fail, raise an error
            raise ValueError("No text found in the PDF file.")

        return md_text
    except Exception as e:
        try:
            if ext == ".pdf":
                # Attempt to extract text using pymupdf4llm
                md_text = pymupdf4llm.to_markdown(file_path)
                md_text = sanitize_string(md_text)
                if not is_valid_text(md_text):
                    raise ValueError("No text found in the PDF file.")
                return md_text
            raise ValueError("No text found in the document file.")
        except Exception as e:
            raise ValueError(f"Failed to extract text from document: {str(e)}")


def map_pages_to_text_offsets(
    pdf_file_path: str,
) -> dict[int, list[int]]:
    """
    Map each page of the PDF to its corresponding text offsets.
    """
    if Path(pdf_file_path).suffix.lower() != ".pdf":
        return {}

    doc = pymupdf.open(pdf_file_path)
    page_offsets = {}
    current_offset = 0

    for page_num in range(len(doc)):
        page = doc[page_num]
        page_text = page.get_text("text")  # type: ignore
        page_length = len(page_text)

        if page_length > 0:
            # Technically, we're only returning Tuples, but the List is easier to work with for json serialization
            page_offsets[page_num + 1] = [current_offset, current_offset + page_length]
            current_offset += page_length

    return page_offsets


def generate_pdf_preview(file_path: str) -> Tuple[str, str]:
    """
    Generate a preview image from the first page of a PDF.

    Args:
        file_path: Path to the PDF file

    Returns:
        tuple[str, str]: The S3 object key and preview URL
    """
    try:
        # Open the PDF from file path
        doc = pymupdf.open(file_path)

        if len(doc) == 0:
            raise Exception("PDF has no pages")

        # Get the first page
        page = doc[0]

        # Render page to a pixmap (image)
        # You can adjust the matrix for different resolution/quality
        mat = pymupdf.Matrix(2.0, 2.0)  # 2x zoom for better quality
        pix = page.get_pixmap(matrix=mat) # type: ignore

        # Convert to PIL Image for easier handling
        img_data = pix.tobytes("png")
        img = Image.open(BytesIO(img_data))

        # Optionally resize to a standard preview size
        # This helps keep file sizes reasonable
        max_width = 800
        if img.width > max_width:
            ratio = max_width / img.width
            new_height = int(img.height * ratio)
            img = img.resize((max_width, new_height), Image.Resampling.LANCZOS) # type: ignore

        # Convert back to bytes
        img_buffer = BytesIO()
        img.save(img_buffer, format="PNG", optimize=True)
        img_buffer.seek(0)

        # Create filename for preview
        preview_filename = f"preview-{uuid.uuid4()}.png"

        # Upload to S3
        preview_object_key, preview_url = s3_service.upload_any_file_from_bytes(
            img_buffer.getvalue(),
            preview_filename,
            content_type="image/png",
        )

        doc.close()
        return preview_object_key, preview_url

    except Exception as e:
        logger.error(f"Error generating PDF preview: {str(e)}")
        raise

async def extract_text(
    file_path: str,
) -> str:
    """
    Extract text from a supported document while replacing images with placeholder IDs.

    Args:
        file_path: Path to the document file

    Returns:
        Tuple[str]:
        - Markdown text with image placeholders
    """
    # If image extraction is disabled, just extract text
    md_text = extract_text_from_document(file_path)
    return md_text
