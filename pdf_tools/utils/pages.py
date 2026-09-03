"""Parse human-entered page ranges like '1-3, 5, 7-10' into validated page indices."""
import re


class PageRangeError(ValueError):
    pass


def parse_page_ranges(text: str, page_count: int) -> list[int]:
    """
    Returns a sorted, de-duplicated list of 0-based page indices.
    Accepts formats like: "1-3,5,7-10" (1-based, inclusive, as shown to users).
    """
    if not text or not text.strip():
        raise PageRangeError('Please enter at least one page or page range.')

    text = text.strip()
    if not re.fullmatch(r'[\d,\-\s]+', text):
        raise PageRangeError('Page ranges may only contain numbers, commas and dashes (e.g. 1-3, 5, 7-10).')

    pages: set[int] = set()
    for part in text.split(','):
        part = part.strip()
        if not part:
            continue
        if '-' in part:
            bits = part.split('-')
            if len(bits) != 2 or not all(b.strip().isdigit() for b in bits):
                raise PageRangeError(f'Invalid range: "{part}".')
            start, end = int(bits[0]), int(bits[1])
            if start > end:
                start, end = end, start
            span = range(start, end + 1)
        else:
            if not part.isdigit():
                raise PageRangeError(f'Invalid page number: "{part}".')
            span = [int(part)]

        for p in span:
            if p < 1 or p > page_count:
                raise PageRangeError(f'Page {p} is out of range. This document has {page_count} pages.')
            pages.add(p - 1)

    if not pages:
        raise PageRangeError('No valid pages were found in your input.')

    return sorted(pages)
