"""
Single source of truth for every tool's slug, display name, description,
icon and category. Homepage, nav menu, sitemap and URLs are all generated
from this list so a new tool only needs to be added in one place.
"""
from django.utils.translation import gettext_lazy as _

CATEGORIES = [
    ('conversion', 'PDF Conversion'),
    ('organization', 'PDF Organization'),
    ('optimization', 'PDF Optimization'),
    ('security', 'PDF Security'),
    ('editing', 'PDF Editing'),
]

TOOLS = [
    # -- Conversion --------------------------------------------------
    {'slug': 'pdf-to-word', 'name': 'PDF to Word', 'category': 'conversion', 'icon': 'file-word',
     'desc': 'Turn your PDF into an editable Word document, structure and formatting preserved.'},
    {'slug': 'pdf-to-jpg', 'name': 'PDF to JPG', 'category': 'conversion', 'icon': 'file-image',
     'desc': 'Convert every page of a PDF into high-quality JPG images.'},
    {'slug': 'pdf-to-png', 'name': 'PDF to PNG', 'category': 'conversion', 'icon': 'file-image',
     'desc': 'Convert PDF pages into crisp, lossless PNG images.'},
    {'slug': 'jpg-to-pdf', 'name': 'JPG to PDF', 'category': 'conversion', 'icon': 'file-pdf',
     'desc': 'Combine one or more JPG images into a single PDF file.'},
    {'slug': 'png-to-pdf', 'name': 'PNG to PDF', 'category': 'conversion', 'icon': 'file-pdf',
     'desc': 'Combine one or more PNG images into a single PDF file.'},
    {'slug': 'pdf-to-text', 'name': 'PDF to Text', 'category': 'conversion', 'icon': 'file-lines',
     'desc': 'Extract all readable text from a PDF into a plain .txt file.'},
    {'slug': 'text-to-pdf', 'name': 'Text to PDF', 'category': 'conversion', 'icon': 'file-pdf',
     'desc': 'Turn plain text into a clean, well-formatted PDF document.'},

    # -- Organization --------------------------------------------------
    {'slug': 'merge-pdf', 'name': 'Merge PDF', 'category': 'organization', 'icon': 'layer-group',
     'desc': 'Combine multiple PDFs into one, in exactly the order you choose.'},
    {'slug': 'split-pdf', 'name': 'Split PDF', 'category': 'organization', 'icon': 'scissors',
     'desc': 'Split a PDF into separate files by page range, or one file per page.'},
    {'slug': 'delete-pages', 'name': 'Delete Pages', 'category': 'organization', 'icon': 'trash',
     'desc': 'Remove specific pages from a PDF using a visual page picker.'},
    {'slug': 'extract-pages', 'name': 'Extract Pages', 'category': 'organization', 'icon': 'file-export',
     'desc': 'Pull out just the pages you need into a brand-new PDF.'},
    {'slug': 'reorder-pages', 'name': 'Reorder Pages', 'category': 'organization', 'icon': 'arrows-up-down',
     'desc': 'Drag and drop to rearrange the pages of a PDF.'},
    {'slug': 'rotate-pdf', 'name': 'Rotate PDF', 'category': 'organization', 'icon': 'rotate',
     'desc': 'Rotate every page, or just the ones you pick, by 90°, 180° or 270°.'},

    # -- Optimization --------------------------------------------------
    {'slug': 'compress-pdf', 'name': 'Compress PDF', 'category': 'optimization', 'icon': 'compress',
     'desc': 'Shrink your PDF file size while keeping it readable and usable.'},

    # -- Security --------------------------------------------------
    {'slug': 'protect-pdf', 'name': 'Protect PDF', 'category': 'security', 'icon': 'lock',
     'desc': 'Add a password so only people you choose can open the file.'},
    {'slug': 'unlock-pdf', 'name': 'Unlock PDF', 'category': 'security', 'icon': 'lock-open',
     'desc': 'Remove a known password from a protected PDF you have the right to access.'},

    # -- Editing --------------------------------------------------
    {'slug': 'watermark-pdf', 'name': 'Watermark PDF', 'category': 'editing', 'icon': 'stamp',
     'desc': 'Stamp a custom text watermark across every page, your way.'},
    {'slug': 'page-numbers', 'name': 'Page Numbers', 'category': 'editing', 'icon': 'list-ol',
     'desc': 'Add clean, consistent page numbers to your PDF.'},
    {'slug': 'edit-metadata', 'name': 'PDF Metadata', 'category': 'editing', 'icon': 'tags',
     'desc': "View and edit a PDF's title, author, subject and keywords."},
    {'slug': 'pdf-info', 'name': 'PDF Info & Preview', 'category': 'editing', 'icon': 'circle-info',
     'desc': 'Inspect page count, size and details, and preview pages before you act.'},
]

TOOLS_BY_SLUG = {t['slug']: t for t in TOOLS}


def tools_by_category():
    grouped = {key: [] for key, _label in CATEGORIES}
    for t in TOOLS:
        grouped.setdefault(t['category'], []).append(t)
    return [(key, label, grouped.get(key, [])) for key, label in CATEGORIES]
