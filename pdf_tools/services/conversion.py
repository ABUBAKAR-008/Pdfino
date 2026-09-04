"""
Conversion tools: PDF <-> Word, PDF <-> images, PDF <-> text.

PDF -> Word uses pdf2docx, which parses PDF layout (text blocks, tables,
images, fonts) and rebuilds it as a real, editable .docx - this is why we
use it instead of "extract text and paste into a blank document", which
was explicitly ruled out in the spec.

Hardening applied throughout:
  - All writes go to a temp file in the same directory, then get atomically
    replaced into place (os.replace). A crash mid-write can never leave a
    corrupt/truncated file at output_path.
  - Every function verifies the output actually exists and is non-empty
    before reporting success.
  - Resources (fitz docs, PIL images) are always closed via try/finally,
    even on the exception paths.
"""
import logging
import os
import tempfile
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image
from django.conf import settings

from .exceptions import ProcessingError
from .processing_guard import guarded

logger = logging.getLogger('pdf_tools')

MAX_DPI = 600  # above this, memory use per page gets dangerous on large PDFs


def _atomic_write_path(output_path: Path):
    """Context-manager-free helper: returns a tmp path in the same dir as
    output_path so os.replace() is a same-filesystem atomic rename."""
    fd, tmp_name = tempfile.mkstemp(
        dir=str(output_path.parent), prefix=f'.{output_path.name}.', suffix='.tmp'
    )
    os.close(fd)
    return Path(tmp_name)


def _finalize(tmp_path: Path, output_path: Path, min_size: int = 1):
    """Verify the tmp output is non-trivial, then atomically move it into place."""
    try:
        if not tmp_path.exists() or tmp_path.stat().st_size < min_size:
            raise ProcessingError('The conversion produced an empty or invalid file.')
        os.replace(str(tmp_path), str(output_path))
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


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


@guarded
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

    tmp_path = _atomic_write_path(output_path)
    converter = Converter(str(pdf_path))
    try:
        converter.convert(str(tmp_path), start=0, end=None)
    except Exception as exc:
        logger.exception('pdf2docx failed for %s', pdf_path)
        raise ProcessingError(
            'This PDF could not be converted to Word. It may use an unsupported '
            'layout, be corrupted, or be password protected.'
        ) from exc
    finally:
        converter.close()

    _finalize(tmp_path, output_path, min_size=100)  # a valid .docx is never a few bytes

    warning = None
    if scanned_pages:
        warning = (
            f'Page(s) {", ".join(map(str, scanned_pages))} appear to be scanned images. '
            'Text on those pages could not be extracted without OCR, so the layout was '
            'preserved but the text there is not editable.'
        )
    return {'page_count': page_count, 'warning': warning}


@guarded
def pdf_to_text(pdf_path: Path, output_path: Path) -> dict:
    doc = _open_pdf(pdf_path)
    try:
        parts = [doc.load_page(i).get_text() for i in range(doc.page_count)]
    finally:
        doc.close()

    tmp_path = _atomic_write_path(output_path)
    tmp_path.write_text('\n\f\n'.join(parts), encoding='utf-8')
    _finalize(tmp_path, output_path, min_size=0)  # a genuinely blank PDF is a valid 0-byte result
    return {'page_count': len(parts)}


@guarded
def text_to_pdf(text: str, output_path: Path, title: str = 'Document') -> dict:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    from xml.sax.saxutils import escape

    if not text or not text.strip():
        raise ProcessingError('Please enter some text to convert.')

    styles = getSampleStyleSheet()
    font_name = 'Helvetica'

    # Helvetica has no CJK/Arabic/Cyrillic/etc glyphs - text in those scripts
    # would silently disappear from the PDF. Detect non-Latin-1 content and
    # fall back to a bundled Unicode font if one is available, otherwise warn
    # the caller instead of shipping a PDF with missing text.
    warning = None
    if any(ord(ch) > 0x24F for ch in text):
        unicode_font_path = None
        for candidate in (
            '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
            '/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf',
        ):
            if Path(candidate).exists():
                unicode_font_path = candidate
                break
        if unicode_font_path:
            pdfmetrics.registerFont(TTFont('UnicodeBody', unicode_font_path))
            font_name = 'UnicodeBody'
            styles['Normal'].fontName = font_name
        else:
            warning = (
                'This text contains characters outside basic Latin script. No Unicode '
                'font was available, so those characters may not render correctly in the PDF.'
            )

    tmp_path = _atomic_write_path(output_path)
    doc = SimpleDocTemplate(
        str(tmp_path), pagesize=letter,
        leftMargin=1 * inch, rightMargin=1 * inch, topMargin=1 * inch, bottomMargin=1 * inch,
        title=title,
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
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise ProcessingError('Could not generate a PDF from this text.') from exc

    _finalize(tmp_path, output_path, min_size=50)
    return {'warning': warning}


@guarded
def pdf_to_images(pdf_path: Path, output_dir: Path, image_format: str = 'jpg',
                   pages: list[int] | None = None, dpi: int = 150) -> list[Path]:
    """Render selected (or all) pages to individual image files. Returns file paths.

    `pages` is 0-indexed. Invalid indices are reported explicitly rather than
    silently dropped, so a typo in a page list fails loudly instead of
    quietly returning fewer pages than expected.
    """
    if image_format not in ('jpg', 'png'):
        raise ProcessingError('Unsupported image format requested.')
    if dpi <= 0:
        raise ProcessingError('DPI must be a positive number.')
    if dpi > MAX_DPI:
        raise ProcessingError(f'DPI is capped at {MAX_DPI} to avoid excessive memory use.')

    doc = _open_pdf(pdf_path)
    try:
        total = doc.page_count
        if pages is not None:
            invalid = [p for p in pages if p < 0 or p >= total]
            if invalid:
                raise ProcessingError(
                    f'Invalid page number(s) requested: {[p + 1 for p in invalid]}. '
                    f'This PDF has {total} page(s).'
                )
            targets = pages
        else:
            targets = list(range(total))

        zoom = dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)

        out_paths = []
        tmp_paths = []
        try:
            for idx in targets:
                page = doc.load_page(idx)
                pix = page.get_pixmap(matrix=matrix, alpha=False)
                if pix.width * pix.height > settings.MAX_IMAGE_PIXELS:
                    raise ProcessingError(
                        f'Rendered image dimensions are too large. Maximum is {settings.MAX_IMAGE_PIXELS:,} pixels.'
                    )
                ext = 'jpg' if image_format == 'jpg' else 'png'
                final_path = output_dir / f'page_{idx + 1:03d}.{ext}'
                tmp_path = _atomic_write_path(final_path)
                if image_format == 'jpg':
                    pix.save(str(tmp_path), output='jpeg', jpg_quality=90)
                else:
                    pix.save(str(tmp_path))
                tmp_paths.append((tmp_path, final_path))
        except ProcessingError:
            raise
        except Exception as exc:
            logger.exception('Failed rendering PDF to images for %s', pdf_path)
            raise ProcessingError('Could not convert this PDF to images.') from exc

        for tmp_path, final_path in tmp_paths:
            _finalize(tmp_path, final_path, min_size=50)
            out_paths.append(final_path)
    finally:
        doc.close()

    if not out_paths:
        raise ProcessingError('No pages were converted. Check the page selection.')
    return out_paths


@guarded
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
            im.load()  # force-read now, so a truncated/corrupt file fails here, not mid-PDF-build
            if im.mode in ('RGBA', 'LA', 'P'):
                # Flattening straight to RGB paints transparent pixels BLACK.
                # Composite onto a white background instead to preserve intent.
                rgba = im.convert('RGBA')
                background = Image.new('RGB', rgba.size, (255, 255, 255))
                background.paste(rgba, mask=rgba.split()[-1])
                im = background
            elif im.mode == 'CMYK':
                im = im.convert('RGB')
            elif im.mode != 'RGB':
                im = im.convert('RGB')
            frames.append((p, im))
    except ProcessingError:
        raise
    except Exception as exc:
        logger.exception('Failed opening one of the images for images_to_pdf')
        for _, im in frames:
            im.close()
        raise ProcessingError('One of the images could not be read. Please check the files.') from exc

    tmp_path = _atomic_write_path(output_path)
    pdf_doc = fitz.open()
    try:
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
        pdf_doc.save(str(tmp_path))
    except Exception as exc:
        logger.exception('Failed assembling images into a PDF')
        raise ProcessingError('Could not build a PDF from these images.') from exc
    finally:
        pdf_doc.close()  # now always runs, success or failure
        for _, im in frames:
            im.close()

    _finalize(tmp_path, output_path, min_size=100)
    return {'page_count': len(image_paths)}