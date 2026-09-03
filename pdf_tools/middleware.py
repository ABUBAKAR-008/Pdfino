import logging

from django.conf import settings
from django.shortcuts import render

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
        if settings.DEBUG:
            return None  # let Django's normal debug page show during development
        logger.exception('Unhandled exception on %s', request.path)
        return render(request, 'pdf_tools/500.html', status=500)
