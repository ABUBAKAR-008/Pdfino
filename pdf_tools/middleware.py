import logging

from django.conf import settings
from django.core.cache import cache
from django.http import Http404, HttpResponse
from django.shortcuts import render

from .services.processing_guard import ProcessingAdmission

logger = logging.getLogger('pdf_tools')


class FriendlyErrorMiddleware:
    """
    Catches anything that slips past view-level try/except blocks so users
    never see a Python traceback. Full details still go to the log file.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if isinstance(exception, Http404):
            return None
        if settings.DEBUG:
            return None  # let Django's normal debug page show during development
        logger.exception('Unhandled exception on %s', request.path)
        return render(request, 'pdf_tools/500.html', status=500)


class ProcessingRateLimitMiddleware:
    """Small per-process abuse guard for synchronous PDF processing endpoints."""

    PROCESSING_PATHS = {
        '/merge-pdf/', '/split-pdf/', '/delete-pages/', '/extract-pages/', '/reorder-pages/',
        '/rotate-pdf/', '/compress-pdf/', '/protect-pdf/', '/unlock-pdf/', '/watermark-pdf/',
        '/page-numbers/', '/edit-metadata/', '/pdf-to-word/', '/pdf-to-text/', '/text-to-pdf/',
        '/pdf-to-jpg/', '/pdf-to-png/', '/jpg-to-pdf/', '/png-to-pdf/', '/pdf-info/',
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == 'POST' and request.path in self.PROCESSING_PATHS:
            identity = f'user:{request.user.pk}' if request.user.is_authenticated else f'ip:{request.META.get("REMOTE_ADDR", "unknown")}'
            limit = settings.RATE_LIMIT_AUTHENTICATED if request.user.is_authenticated else settings.RATE_LIMIT_ANONYMOUS
            if request.path in {'/pdf-to-word/', '/pdf-to-jpg/', '/pdf-to-png/', '/jpg-to-pdf/', '/png-to-pdf/', '/compress-pdf/'}:
                limit = min(limit, settings.RATE_LIMIT_EXPENSIVE)
            key = f'pdfino-rate:{identity}:{request.path}'
            count = cache.get(key, 0)
            if count >= limit:
                return HttpResponse('Too many processing requests. Please wait a moment and try again.', status=429)
            cache.set(key, count + 1, settings.RATE_LIMIT_WINDOW_SECONDS)
            if not ProcessingAdmission.acquire(identity):
                response = HttpResponse(
                    'Processing capacity is busy. Please wait a moment and try again.', status=429
                )
                response['Retry-After'] = '1'
                return response
            try:
                return self.get_response(request)
            finally:
                ProcessingAdmission.release(identity)
        return self.get_response(request)
