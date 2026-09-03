"""
Conversion tools: PDF <-> Word, PDF <-> images, PDF <-> text.

PDF -> Word uses pdf2docx, which parses PDF layout (text blocks, tables,
images, fonts) and rebuilds it as a real, editable .docx - this is why we
use it instead of "extract text and paste into a blank document", which
was explicitly ruled out in the spec.
"""
import logging
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image

from .exceptions import ProcessingError

logger = logging.getLogger('pdf_tools')


def _open_pdf(path: Path):
    try:
        doc = fitz.open(path)
    except Exception as exc:
        logger.exception('Failed to open PDF %s', path)
        raise ProcessingError(
            'Unable to open this PDF. The file may be corrupted or password protected.'
        ) from exc
    if doc.is_encrypted:
        doc.close()
        raise ProcessingError(
            'This PDF is password protected. Please unlock it first using the Unlock PDF tool.'
        )
    if doc.page_count == 0:
        doc.close()
        raise ProcessingError('This PDF has no pages to process.')
    return doc


def pdf_to_word(pdf_path: Path, output_path: Path) -> dict:
    """
    Convert PDF to an editable, structure-preserving .docx using pdf2docx.
    Preserves paragraphs, headings, tables, images and approximate layout.
    Returns a small report noting any pages pdf2docx could not fully parse.
    """
    from pdf2docx import Converter

    doc = _open_pdf(pdf_path)
    page_count = doc.page_count
    # detect pages that are pure images (likely scanned) - pdf2docx cannot
    # OCR these, so we warn the user rather than silently failing.
    scanned_pages = []
    for i in range(page_count):
        page = doc.load_page(i)
        text = page.get_text().strip()
        if not text and page.get_images():
            scanned_pages.append(i + 1)
    doc.close()

    converter = Converter(str(pdf_path))
    try:
        converter.convert(str(output_path), start=0, end=None)
    except Exception as exc:
        logger.exception('pdf2docx failed for %s', pdf_path)
        raise ProcessingError(
            'This PDF could not be converted to Word. It may use an unsupported '
            'layout, be corrupted, or be password protected.'
        ) from exc
    finally:
        converter.close()

    warning = None
    if scanned_pages:
        warning = (
            f'Page(s) {", ".join(map(str, scanned_pages))} appear to be scanned images. '
            'Text on those pages could not be extracted without OCR, so the layout was '
            'preserved but the text there is not editable.'
        )
    return {'page_count': page_count, 'warning': warning}


def pdf_to_text(pdf_path: Path, output_path: Path) -> dict:
    doc = _open_pdf(pdf_path)
    parts = []
    for i in range(doc.page_count):
        parts.append(doc.load_page(i).get_text())
    doc.close()
    output_path.write_text('\n\f\n'.join(parts), encoding='utf-8')
    return {'page_count': len(parts)}


def text_to_pdf(text: str, output_path: Path, title: str = 'Document') -> dict:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    from xml.sax.saxutils import escape

    if not text or not text.strip():
        raise ProcessingError('Please enter some text to convert.')

    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(
        str(output_path), pagesize=letter,
        leftMargin=1 * inch, rightMargin=1 * inch, topMargin=1 * inch, bottomMargin=1 * inch,
    )
    story = []
    for para in text.split('\n\n'):
        safe = escape(para).replace('\n', '<br/>')
        story.append(Paragraph(safe or '&nbsp;', styles['Normal']))
        story.append(Spacer(1, 12))
    try:
        doc.build(story)
    except Exception as exc:
        logger.exception('reportlab failed building text-to-pdf')
        raise ProcessingError('Could not generate a PDF from this text.') from exc
    return {}


def pdf_to_images(pdf_path: Path, output_dir: Path, image_format: str = 'jpg',
                   pages: list[int] | None = None, dpi: int = 150) -> list[Path]:
    """Render selected (or all) pages to individual image files. Returns file paths."""
    if image_format not in ('jpg', 'png'):
        raise ProcessingError('Unsupported image format requested.')

    doc = _open_pdf(pdf_path)
    total = doc.page_count
    targets = pages if pages is not None else list(range(total))
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)

    out_paths = []
    try:
        for idx in targets:
            if idx < 0 or idx >= total:
                continue
            page = doc.load_page(idx)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            ext = 'jpg' if image_format == 'jpg' else 'png'
            out_path = output_dir / f'page_{idx + 1:03d}.{ext}'
            if image_format == 'jpg':
                pix.save(str(out_path), output='jpeg', jpg_quality=90)
            else:
                pix.save(str(out_path))
            out_paths.append(out_path)
    except ProcessingError:
        raise
    except Exception as exc:
        logger.exception('Failed rendering PDF to images for %s', pdf_path)
        raise ProcessingError('Could not convert this PDF to images.') from exc
    finally:
        doc.close()

    if not out_paths:
        raise ProcessingError('No pages were converted. Check the page selection.')
    return out_paths


def images_to_pdf(image_paths: list[Path], output_path: Path, page_size: str = 'a4',
                   orientation: str = 'portrait', margin_mm: int = 0, fit: str = 'contain') -> dict:
    """Combine one or more JPG/PNG images into a single PDF."""
    if not image_paths:
        raise ProcessingError('Please add at least one image.')

    sizes_pt = {'a4': (595.28, 841.89), 'letter': (612, 792), 'auto': None}
    if page_size not in sizes_pt:
        page_size = 'a4'

    mm_to_pt = 2.83465
    margin = margin_mm * mm_to_pt

    frames = []
    try:
        for p in image_paths:
            im = Image.open(p)
            im = im.convert('RGB') if im.mode in ('RGBA', 'P') else im
            frames.append((p, im))
    except Exception as exc:
        logger.exception('Failed opening one of the images for images_to_pdf')
        raise ProcessingError('One of the images could not be read. Please check the files.') from exc

    try:
        pdf_doc = fitz.open()
        for path, im in frames:
            iw, ih = im.size
            if page_size == 'auto':
                page_w, page_h = iw, ih
                m = 0
            else:
                page_w, page_h = sizes_pt[page_size]
                if orientation == 'landscape':
                    page_w, page_h = page_h, page_w
                m = margin

            page = pdf_doc.new_page(width=page_w, height=page_h)
            avail_w, avail_h = page_w - 2 * m, page_h - 2 * m

            if fit == 'stretch':
                rect = fitz.Rect(m, m, m + avail_w, m + avail_h)
            else:  # contain - preserve aspect ratio
                scale = min(avail_w / iw, avail_h / ih)
                dw, dh = iw * scale, ih * scale
                x0 = m + (avail_w - dw) / 2
                y0 = m + (avail_h - dh) / 2
                rect = fitz.Rect(x0, y0, x0 + dw, y0 + dh)

            page.insert_image(rect, filename=str(path))
        pdf_doc.save(str(output_path))
        pdf_doc.close()
    except Exception as exc:
        logger.exception('Failed assembling images into a PDF')
        raise ProcessingError('Could not build a PDF from these images.') from exc
    finally:
        for _, im in frames:
            im.close()

    return {'page_count': len(image_paths)}
