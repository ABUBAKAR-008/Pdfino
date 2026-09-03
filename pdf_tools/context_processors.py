from .tool_registry import tools_by_category


def site_meta(request):
    return {
        'SITE_NAME': 'Pdfino',
        'SITE_TAGLINE': 'Every PDF tool you need, in one place.',
        'TOOL_CATEGORIES': tools_by_category(),
    }
