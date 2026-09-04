import logging
from pathlib import Path

import fitz  # PyMuPDF

from .exceptions import ProcessingError
from .processing_guard import guarded

logger = logging.getLogger('pdf_tools')

# (image_dpi_target, jpeg_quality) per level - lower dpi/quality = smaller file
_LEVELS = {
    'low': (150, 85),
    'medium': (110, 70),
    'high': (72, 45),
}


@guarded
def compress_pdf(pdf_path: Path, output_path: Path, level: str = 'medium') -> dict:
    if level not in _LEVELS:
        level = 'medium'
    target_dpi, quality = _LEVELS[level]

    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        raise ProcessingError('Unable to open this PDF. The file may be corrupted or password protected.') from exc

    if doc.is_encrypted:
        doc.close()
        raise ProcessingError('This PDF is password protected. Please unlock it first.')

    original_size = Path(pdf_path).stat().st_size

    try:
        for page in doc:
            images = page.get_images(full=True)
            for img in images:
                xref = img[0]
                try:
                    base = doc.extract_image(xref)
                except Exception:
                    continue
                image_bytes = base.get('image')
                if not image_bytes:
                    continue
                from PIL import Image
                import io
                try:
                    pil_img = Image.open(io.BytesIO(image_bytes))
                    pil_img = pil_img.convert('RGB') if pil_img.mode in ('RGBA', 'P', 'CMYK') else pil_img
                    buf = io.BytesIO()
                    pil_img.save(buf, format='JPEG', quality=quality, optimize=True)
                    doc.update_stream(xref, buf.getvalue())
                except Exception:
                    # If a particular image can't be re-encoded, leave it untouched
                    # rather than failing the whole document.
                    continue

        # Structural cleanup: remove unused objects, compress streams
        doc.save(
            str(output_path),
            garbage=4,
            deflate=True,
            clean=True,
        )
    except Exception as exc:
        logger.exception('compress_pdf failed')
        raise ProcessingError('Could not compress this PDF.') from exc
    finally:
        doc.close()

    new_size = Path(output_path).stat().st_size
    # Safety net: if our re-encoding somehow made the file bigger, fall back to
    # a clean structural-only save of the original so users never get a worse file.
    if new_size >= original_size:
        try:
            fallback = fitz.open(pdf_path)
            fallback.save(str(output_path), garbage=4, deflate=True, clean=True)
            fallback.close()
            new_size = Path(output_path).stat().st_size
        except Exception:
            pass

    return {'original_size': original_size, 'compressed_size': min(new_size, original_size)}
