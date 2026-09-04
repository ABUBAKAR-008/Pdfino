import logging
from pathlib import Path

import fitz  # PyMuPDF

from .exceptions import ProcessingError
from .processing_guard import guarded

logger = logging.getLogger('pdf_tools')

_POSITIONS = {
    'center': (0.5, 0.5),
    'top-left': (0.12, 0.1),
    'top-right': (0.88, 0.1),
    'bottom-left': (0.12, 0.9),
    'bottom-right': (0.88, 0.9),
}


def _open(path: Path):
    try:
        doc = fitz.open(path)
    except Exception as exc:
        raise ProcessingError('Unable to open this PDF. The file may be corrupted or password protected.') from exc
    if doc.is_encrypted:
        doc.close()
        raise ProcessingError('This PDF is password protected. Please unlock it first.')
    return doc


@guarded
def watermark_pdf(pdf_path: Path, output_path: Path, text: str, *, font_size: int = 40,
                   opacity: float = 0.3, rotation: int = 45, color: tuple = (0.5, 0.5, 0.5),
                   position: str = 'center', page_indices: list[int] | None = None) -> dict:
    if not text or not text.strip():
        raise ProcessingError('Please enter watermark text.')

    doc = _open(pdf_path)
    try:
        targets = page_indices if page_indices is not None else list(range(doc.page_count))
        for idx in targets:
            if not (0 <= idx < doc.page_count):
                continue
            page = doc.load_page(idx)
            rect = page.rect
            fx, fy = _POSITIONS.get(position, _POSITIONS['center'])
            point = fitz.Point(rect.width * fx, rect.height * fy)

            shape = page.new_shape()
            morph = (point, fitz.Matrix(rotation))
            shape.insert_text(
                point, text, fontsize=font_size, fontname='helv',
                color=color, fill_opacity=max(0.05, min(opacity, 1)),
                morph=morph,
            )
            shape.commit(overlay=True)
        doc.save(str(output_path))
    except Exception as exc:
        logger.exception('watermark_pdf failed')
        raise ProcessingError('Could not add a watermark to this PDF.') from exc
    finally:
        doc.close()
    return {'pages_watermarked': len(targets)}


@guarded
def add_page_numbers(pdf_path: Path, output_path: Path, *, position: str = 'bottom-right',
                      start_at: int = 1, font_size: int = 11) -> dict:
    doc = _open(pdf_path)
    try:
        total = doc.page_count
        for i in range(total):
            page = doc.load_page(i)
            rect = page.rect
            label = f'{start_at + i}'
            fx, fy = _POSITIONS.get(position, _POSITIONS['bottom-right'])
            x = rect.width * fx
            y = rect.height * (0.95 if 'bottom' in position else 0.06)
            page.insert_text((x, y), label, fontsize=font_size, fontname='helv', color=(0, 0, 0))
        doc.save(str(output_path))
    except Exception as exc:
        logger.exception('add_page_numbers failed')
        raise ProcessingError('Could not add page numbers to this PDF.') from exc
    finally:
        doc.close()
    return {'page_count': total}


@guarded
def get_pdf_info(pdf_path: Path) -> dict:
    doc = _open(pdf_path)
    try:
        page0 = doc.load_page(0)
        info = {
            'page_count': doc.page_count,
            'is_encrypted': doc.is_encrypted,
            'metadata': dict(doc.metadata or {}),
            'page_width_pt': round(page0.rect.width, 1),
            'page_height_pt': round(page0.rect.height, 1),
            'has_text': any(doc.load_page(i).get_text().strip() for i in range(min(doc.page_count, 5))),
        }
    finally:
        doc.close()
    return info


@guarded
def render_thumbnails(pdf_path: Path, output_dir: Path, *, max_pages: int = 60, dpi: int = 60) -> list[Path]:
    """Small page thumbnails used by the page-management / preview UI."""
    doc = _open(pdf_path)
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    out = []
    try:
        for i in range(min(doc.page_count, max_pages)):
            page = doc.load_page(i)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            path = output_dir / f'thumb_{i + 1:03d}.jpg'
            pix.save(str(path), output='jpeg', jpg_quality=70)
            out.append(path)
    except Exception as exc:
        logger.exception('render_thumbnails failed')
        raise ProcessingError('Could not generate a preview for this PDF.') from exc
    finally:
        doc.close()
    return out
