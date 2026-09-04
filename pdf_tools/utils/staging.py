from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

from ..models import StagedDocument
from . import files


def _session_key(request):
    if not request.session.session_key:
        request.session.save()
    return request.session.session_key


def stage_upload(request, uploaded_file):
    files.validate_pdf_upload(uploaded_file)
    staging_dir = Path(settings.STAGING_TMP_DIR) / files.random_name('stage').split('.')[0]
    staging_dir.mkdir(parents=True, exist_ok=False)
    path = files.save_upload(uploaded_file, staging_dir, 'pdf')
    staged = StagedDocument.objects.create(
        user=request.user if request.user.is_authenticated else None,
        session_key='' if request.user.is_authenticated else _session_key(request),
        relpath=str(path.relative_to(settings.STAGING_TMP_DIR)),
        original_filename=uploaded_file.name[:255],
        size_bytes=uploaded_file.size,
        expires_at=timezone.now() + timedelta(minutes=settings.STAGING_RETENTION_MINUTES),
    )
    return staged


def get_staged(request, token):
    try:
        staged = StagedDocument.objects.get(token=token, expires_at__gt=timezone.now())
    except (StagedDocument.DoesNotExist, ValueError, TypeError, ValidationError):
        return None
    if request.user.is_authenticated:
        if staged.user_id != request.user.id:
            return None
    elif staged.user_id is not None or staged.session_key != _session_key(request):
        return None
    root = Path(settings.STAGING_TMP_DIR).resolve()
    path = (root / staged.relpath).resolve()
    if root not in path.parents or not path.is_file():
        return None
    return staged, path


def delete_staged(staged):
    root = Path(settings.STAGING_TMP_DIR).resolve()
    path = (root / staged.relpath).resolve()
    if root in path.parents and path.parent != root:
        files.cleanup_dir(path.parent)
    staged.delete()