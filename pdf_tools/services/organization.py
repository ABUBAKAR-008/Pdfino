import logging
from pathlib import Path

import fitz  # PyMuPDF

from .exceptions import ProcessingError

logger = logging.getLogger('pdf_tools')


def _open(path: Path):
    try:
        doc = fitz.open(path)
    except Exception as exc:
        raise ProcessingError('Unable to open this PDF. The file may be corrupted or password protected.') from exc
    if doc.is_encrypted:
        doc.close()
        raise ProcessingError('This PDF is password protected. Please unlock it first.')
    return doc


def merge_pdfs(pdf_paths: list[Path], output_path: Path) -> dict:
    if len(pdf_paths) < 2:
        raise ProcessingError('Please add at least two PDF files to merge.')

    result = fitz.open()
    opened = []
    try:
        for p in pdf_paths:
            d = _open(p)
            opened.append(d)
            result.insert_pdf(d)
        result.save(str(output_path))
    except ProcessingError:
        raise
    except Exception as exc:
        logger.exception('merge_pdfs failed')
        raise ProcessingError('Could not merge these PDFs. Please make sure all files are valid.') from exc
    finally:
        for d in opened:
            d.close()
        result.close()

    return {'file_count': len(pdf_paths)}


def split_by_ranges(pdf_path: Path, ranges: list[list[int]], output_dir: Path) -> list[Path]:
    """ranges: list of 0-based page-index lists, one per output file."""
    doc = _open(pdf_path)
    out_paths = []
    try:
        for n, page_indices in enumerate(ranges, start=1):
            part = fitz.open()
            for idx in page_indices:
                part.insert_pdf(doc, from_page=idx, to_page=idx)
            out_path = output_dir / f'part_{n:02d}.pdf'
            part.save(str(out_path))
            part.close()
            out_paths.append(out_path)
    except Exception as exc:
        logger.exception('split_by_ranges failed')
        raise ProcessingError('Could not split this PDF.') from exc
    finally:
        doc.close()
    return out_paths


def split_every_page(pdf_path: Path, output_dir: Path) -> list[Path]:
    doc = _open(pdf_path)
    total = doc.page_count
    doc.close()
    ranges = [[i] for i in range(total)]
    return split_by_ranges(pdf_path, ranges, output_dir)


def rotate_pdf(pdf_path: Path, output_path: Path, degrees: int, page_indices: list[int] | None) -> dict:
    if degrees % 90 != 0:
        raise ProcessingError('Rotation must be 90, 180 or 270 degrees.')

    doc = _open(pdf_path)
    try:
        targets = page_indices if page_indices is not None else list(range(doc.page_count))
        for idx in targets:
            if 0 <= idx < doc.page_count:
                page = doc.load_page(idx)
                page.set_rotation((page.rotation + degrees) % 360)
        doc.save(str(output_path))
    except Exception as exc:
        logger.exception('rotate_pdf failed')
        raise ProcessingError('Could not rotate this PDF.') from exc
    finally:
        doc.close()
    return {'rotated_pages': len(targets)}


def delete_pages(pdf_path: Path, output_path: Path, page_indices: list[int]) -> dict:
    doc = _open(pdf_path)
    try:
        if len(page_indices) >= doc.page_count:
            raise ProcessingError('You cannot delete every page in the document.')
        doc.delete_pages(page_indices)
        doc.save(str(output_path))
        remaining = doc.page_count
    except ProcessingError:
        raise
    except Exception as exc:
        logger.exception('delete_pages failed')
        raise ProcessingError('Could not delete the selected pages.') from exc
    finally:
        doc.close()
    return {'remaining_pages': remaining}


def extract_pages(pdf_path: Path, output_path: Path, page_indices: list[int]) -> dict:
    doc = _open(pdf_path)
    try:
        new_doc = fitz.open()
        for idx in page_indices:
            new_doc.insert_pdf(doc, from_page=idx, to_page=idx)
        new_doc.save(str(output_path))
        new_doc.close()
    except Exception as exc:
        logger.exception('extract_pages failed')
        raise ProcessingError('Could not extract the selected pages.') from exc
    finally:
        doc.close()
    return {'extracted_pages': len(page_indices)}


def reorder_pages(pdf_path: Path, output_path: Path, new_order: list[int]) -> dict:
    """new_order is a full permutation of 0-based page indices in the desired final order."""
    doc = _open(pdf_path)
    try:
        if sorted(new_order) != list(range(doc.page_count)):
            raise ProcessingError('The new page order must include every page exactly once.')
        doc.select(new_order)
        doc.save(str(output_path))
    except ProcessingError:
        raise
    except Exception as exc:
        logger.exception('reorder_pages failed')
        raise ProcessingError('Could not reorder the pages.') from exc
    finally:
        doc.close()
    return {'page_count': len(new_order)}


def get_page_count(pdf_path: Path) -> int:
    doc = _open(pdf_path)
    n = doc.page_count
    doc.close()
    return n
