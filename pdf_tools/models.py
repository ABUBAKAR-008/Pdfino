import uuid

from django.conf import settings
from django.db import models


class ToolCategory(models.TextChoices):
    CONVERSION = 'conversion', 'PDF Conversion'
    ORGANIZATION = 'organization', 'PDF Organization'
    OPTIMIZATION = 'optimization', 'PDF Optimization'
    SECURITY = 'security', 'PDF Security'
    EDITING = 'editing', 'PDF Editing'


class ConversionJob(models.Model):
    """
    One record per processing request. We deliberately store metadata only -
    never the file contents - so nothing sensitive lives in the database.
    """

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        PROCESSING = 'processing', 'Processing'
        SUCCESS = 'success', 'Success'
        FAILED = 'failed', 'Failed'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='jobs',
    )
    tool_slug = models.CharField(max_length=64, db_index=True)
    tool_name = models.CharField(max_length=128)
    category = models.CharField(max_length=32, choices=ToolCategory.choices, blank=True)

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    error_message = models.CharField(max_length=500, blank=True)

    original_filename = models.CharField(max_length=255, blank=True)
    original_size_bytes = models.PositiveBigIntegerField(default=0)
    result_filename = models.CharField(max_length=255, blank=True)
    result_size_bytes = models.PositiveBigIntegerField(default=0)

    # Path is relative to OUTPUT_TMP_DIR, never a full filesystem path shown to users
    result_relpath = models.CharField(max_length=255, blank=True)

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['tool_slug', 'created_at']),
            models.Index(fields=['status', 'expires_at']),
        ]

    def __str__(self):
        return f'{self.tool_name} [{self.status}] {self.id}'

    @property
    def size_reduction_percent(self):
        if self.original_size_bytes and self.result_size_bytes:
            reduced = self.original_size_bytes - self.result_size_bytes
            return round((reduced / self.original_size_bytes) * 100, 1)
        return None


class Profile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'Profile<{self.user.username}>'
