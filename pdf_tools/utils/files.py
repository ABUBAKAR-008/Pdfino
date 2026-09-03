"""
File-safety helpers used by every view/service.

Rules enforced here:
- Never trust a client-supplied filename or extension on its own.
- Always generate our own random, collision-free names for anything written to disk.
- Validate real file content (magic bytes), not just extensions.
- Keep every temp file inside MEDIA_ROOT/uploads or MEDIA_ROOT/outputs - never
  let user input influence the directory path (blocks path traversal).
"""
import mimetypes
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError

# Magic-byte signatures for the formats Pdfino accepts as input.
_SIGNATURES = {
    'pdf': (b'%PDF-',),
    'jpg': (b'\xff\xd8\xff',),
    'png': (b'\x89PNG\r\n\x1a\n',),
}

ALLOWED_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png'}
ALLOWED_PDF_EXTENSION = '.pdf'


class UnsafeFileError(ValidationError):
    """Raised when an uploaded file fails validation."""


def safe_stem(original_name: str) -> str:
    """Return a short, filesystem-safe stem derived from the user's filename,
    for display purposes ONLY - never used to build an actual disk path."""
    stem = Path(original_name or 'file').stem
    stem = re.sub(r'[^A-Za-z0-9._-]+', '_', stem).strip('._') or 'file'
    return stem[:60]


def random_name(extension: str) -> str:
    extension = extension.lstrip('.')
    return f'{uuid.uuid4().hex}.{extension}'


def _job_dir(base: Path) -> Path:
    """Create a fresh, uniquely-named subdirectory for one job's temp files."""
    d = base / uuid.uuid4().hex
    d.mkdir(parents=True, exist_ok=False)
    return d


def new_upload_dir() -> Path:
    return _job_dir(Path(settings.UPLOAD_TMP_DIR))


def new_output_dir() -> Path:
    return _job_dir(Path(settings.OUTPUT_TMP_DIR))


def sniff_kind(fileobj) -> str | None:
    """Peek at magic bytes to determine real file type, regardless of extension."""
    pos = fileobj.tell()
    header = fileobj.read(16)
    fileobj.seek(pos)
    for kind, sigs in _SIGNATURES.items():
        if any(header.startswith(sig) for sig in sigs):
            return kind
    return None


def validate_pdf_upload(django_file, max_size=None):
    """Validate that an uploaded file is really a PDF and within size limits."""
    max_size = max_size or settings.MAX_UPLOAD_SIZE
    if django_file.size == 0:
        raise UnsafeFileError('The uploaded file is empty.')
    if django_file.size > max_size:
        mb = max_size // (1024 * 1024)
        raise UnsafeFileError(f'File is too large. Maximum allowed size is {mb} MB.')
    kind = sniff_kind(django_file)
    if kind != 'pdf':
        raise UnsafeFileError('This file does not look like a valid PDF.')
    return True


def validate_image_upload(django_file, max_size=None):
    max_size = max_size or settings.MAX_UPLOAD_SIZE
    if django_file.size == 0:
        raise UnsafeFileError('The uploaded file is empty.')
    if django_file.size > max_size:
        mb = max_size // (1024 * 1024)
        raise UnsafeFileError(f'File is too large. Maximum allowed size is {mb} MB.')
    kind = sniff_kind(django_file)
    if kind not in ('jpg', 'png'):
        raise UnsafeFileError('Only JPG and PNG images are supported.')
    return True


def save_upload(django_file, directory: Path, extension: str) -> Path:
    """Persist an uploaded file under a random name inside `directory`."""
    dest = directory / random_name(extension)
    with open(dest, 'wb') as out:
        for chunk in django_file.chunks():
            out.write(chunk)
    return dest


def cleanup_dir(path: Path):
    """Best-effort recursive delete; never raises."""
    try:
        if path and Path(path).exists():
            shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024 or unit == 'GB':
            return f'{size:.1f} {unit}' if unit != 'B' else f'{int(size)} {unit}'
        size /= 1024
    return f'{size:.1f} GB'


def guess_content_type(path: Path) -> str:
    ctype, _ = mimetypes.guess_type(str(path))
    return ctype or 'application/octet-stream'
