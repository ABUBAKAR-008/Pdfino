"""
Deletes generated output files whose retention window has passed, plus any
orphaned upload/output subdirectories older than the retention window
(a safety net for jobs that errored before cleanup ran).

Intended to run on a schedule, e.g. every 15 minutes via cron:

    */15 * * * * /path/to/.venv/bin/python /path/to/manage.py cleanup_expired_files
"""
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from pdf_tools.models import ConversionJob
from pdf_tools.utils.files import cleanup_dir


class Command(BaseCommand):
    help = 'Deletes expired Pdfino output files and stale temp directories.'

    def handle(self, *args, **options):
        now = timezone.now()
        expired_jobs = ConversionJob.objects.filter(
            status=ConversionJob.Status.SUCCESS,
            expires_at__lt=now,
            result_relpath__gt='',
        )
        removed = 0
        for job in expired_jobs:
            path = Path(settings.OUTPUT_TMP_DIR) / job.result_relpath
            cleanup_dir(path.parent)
            job.result_relpath = ''
            job.result_filename = ''
            job.save(update_fields=['result_relpath', 'result_filename'])
            removed += 1

        # Safety net: remove any leftover directories older than 2x the
        # retention window, regardless of DB state (covers crashed requests).
        cutoff = time.time() - (settings.FILE_RETENTION_MINUTES * 2 * 60)
        stale_dirs = 0
        for base in (settings.UPLOAD_TMP_DIR, settings.OUTPUT_TMP_DIR):
            base = Path(base)
            if not base.exists():
                continue
            for child in base.iterdir():
                if child.is_dir() and child.stat().st_mtime < cutoff:
                    cleanup_dir(child)
                    stale_dirs += 1

        self.stdout.write(self.style.SUCCESS(
            f'Cleaned {removed} expired job(s) and {stale_dirs} stale temp folder(s).'
        ))
