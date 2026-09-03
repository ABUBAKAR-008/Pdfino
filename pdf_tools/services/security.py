import logging
from pathlib import Path

import fitz  # PyMuPDF

from .exceptions import ProcessingError

logger = logging.getLogger('pdf_tools')


def protect_pdf(pdf_path: Path, output_path: Path, password: str) -> dict:
    if not password or len(password) < 4:
        raise ProcessingError('Please choose a password with at least 4 characters.')

    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        raise ProcessingError('Unable to open this PDF. The file may be corrupted.') from exc

    if doc.is_encrypted:
        doc.close()
        raise ProcessingError('This PDF is already password protected.')

    try:
        perm = int(
            fitz.PDF_PERM_PRINT | fitz.PDF_PERM_COPY | fitz.PDF_PERM_ANNOTATE
        )
        doc.save(
            str(output_path),
            encryption=fitz.PDF_ENCRYPT_AES_256,
            owner_pw=password,
            user_pw=password,
            permissions=perm,
        )
    except Exception as exc:
        logger.exception('protect_pdf failed')
        raise ProcessingError('Could not protect this PDF with a password.') from exc
    finally:
        doc.close()
    # Never log the password itself.
    return {}


def unlock_pdf(pdf_path: Path, output_path: Path, password: str) -> dict:
    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        raise ProcessingError('Unable to open this PDF. The file may be corrupted.') from exc

    if not doc.is_encrypted:
        doc.close()
        raise ProcessingError('This PDF is not password protected.')

    try:
        ok = doc.authenticate(password or '')
        if not ok:
            raise ProcessingError('Incorrect password. Please check it and try again.')
        doc.save(str(output_path))
    except ProcessingError:
        raise
    except Exception as exc:
        logger.exception('unlock_pdf failed')
        raise ProcessingError('Could not unlock this PDF.') from exc
    finally:
        doc.close()
    return {}


def get_metadata(pdf_path: Path) -> dict:
    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        raise ProcessingError('Unable to open this PDF. The file may be corrupted or password protected.') from exc
    if doc.is_encrypted:
        doc.close()
        raise ProcessingError('This PDF is password protected. Please unlock it first.')
    meta = dict(doc.metadata or {})
    doc.close()
    return {
        'title': meta.get('title', '') or '',
        'author': meta.get('author', '') or '',
        'subject': meta.get('subject', '') or '',
        'keywords': meta.get('keywords', '') or '',
        'creator': meta.get('creator', '') or '',
        'producer': meta.get('producer', '') or '',
    }


def set_metadata(pdf_path: Path, output_path: Path, fields: dict) -> dict:
    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        raise ProcessingError('Unable to open this PDF. The file may be corrupted.') from exc
    if doc.is_encrypted:
        doc.close()
        raise ProcessingError('This PDF is password protected. Please unlock it first.')
    try:
        doc.set_metadata({
            'title': fields.get('title', ''),
            'author': fields.get('author', ''),
            'subject': fields.get('subject', ''),
            'keywords': fields.get('keywords', ''),
            'creator': fields.get('creator', 'Pdfino'),
            'producer': 'Pdfino',
        })
        doc.save(str(output_path))
    except Exception as exc:
        logger.exception('set_metadata failed')
        raise ProcessingError('Could not update the metadata for this PDF.') from exc
    finally:
        doc.close()
    return {}
