# Pdfino

Pdfino is a free, browser-based PDF toolkit built with Django - merge, split,
compress, convert and edit PDF files, similar in spirit to sites like
iLovePDF. Every tool performs real document processing on the server; there
are no fake buttons or placeholder results.

## Features

- **Conversion:** PDF ↔ Word, PDF ↔ Text, PDF ↔ JPG/PNG, JPG/PNG → PDF
- **Organization:** Merge, Split, Delete pages, Extract pages, Reorder pages, Rotate
- **Optimization:** Compress PDF (low/medium/high, with size-reduction reporting)
- **Security:** Password-protect PDF, Unlock PDF, edit metadata
- **Editing:** Watermark PDF, page numbering, PDF info/preview
- Drag-and-drop uploads with real client-side upload progress
- Automatic, scheduled cleanup of every temporary file
- Optional accounts with a conversion-history dashboard
- Responsive, accessible UI with a dark mode toggle

## Requirements

- Python 3.12+ (3.10+ should also work)
- pip
- (Optional, production) PostgreSQL

## PDF libraries used

| Library | Used for |
|---|---|
| PyMuPDF (`fitz`) | Merge, split, rotate, delete/extract/reorder pages, PDF→image, compression, watermark, page numbers, encryption/decryption, metadata, PDF info |
| pdf2docx | PDF → Word, preserving layout/tables/images as an editable `.docx` |
| Pillow | Image validation, image re-encoding for compression, image → PDF |
| reportlab | Text → PDF generation |
| python-docx | Available for any further Word-document post-processing |
| openpyxl | Available for future Excel-related functionality |

## Known technical limitations

- **PDF → Word** cannot make *scanned* (image-only) pages editable without
  OCR, which is not included by default. The tool detects this and shows a
  clear warning naming the affected pages rather than silently failing.
- **PDF → Excel** is not implemented as a distinct tool in this build; PDF →
  Word (which handles tables) and PDF → Text cover most extraction needs.
  `openpyxl` is already a dependency if you want to add it.
- **Repair PDF** is not implemented as a separate tool; Compress PDF already
  performs a structural clean/rebuild (`garbage=4, clean=True`) which fixes
  many malformed-PDF issues as a side effect.
- **Unlock PDF** requires the current password - it is a convenience tool for
  files you're authorized to access, not a password-recovery tool.

## Installation

### 1. Clone/extract the project and create a virtual environment

**macOS / Linux:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

**Windows (PowerShell):**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks the activation script, run this once as Administrator:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

```bash
cp .env.example .env      # macOS/Linux
copy .env.example .env    # Windows
```
Edit `.env` and set a real `SECRET_KEY` (any long random string). The
defaults work fine for local development as-is.

### 4. Set up the database

```bash
python manage.py migrate
```

### 5. Create an admin account (optional, for Django admin access)

```bash
python manage.py createsuperuser
```

### 6. Run the development server

```bash
python manage.py runserver
```

Visit **http://127.0.0.1:8000/** in your browser.

## Running tests

```bash
python manage.py test
```

This runs unit tests for file validation, page-range parsing, every PDF
service function (merge, split, rotate, compress, protect/unlock, watermark,
metadata, conversions, etc.), and smoke tests that every page in the site
loads correctly.

## File cleanup

Uploaded files are processed in a temporary per-request directory and
deleted immediately after processing (success or failure) - they are never
kept longer than a single request.

Generated *output* files (the file you download) are kept for
`FILE_RETENTION_MINUTES` (default 60) so you have time to download them, then
removed automatically. Run this on a schedule (cron, Windows Task Scheduler,
or your hosting platform's scheduler) to actually perform the deletion:

```bash
python manage.py cleanup_expired_files
```

Example cron entry (every 15 minutes):
```
*/15 * * * * /path/to/.venv/bin/python /path/to/manage.py cleanup_expired_files
```

## Production deployment guidance

1. Set `DEBUG=False` in your environment.
2. Set a strong, unique `SECRET_KEY`.
3. Set `ALLOWED_HOSTS` to your real domain(s).
4. Point `DATABASE_URL` at a PostgreSQL instance, e.g.
   `postgres://user:password@host:5432/pdfino` - no code changes needed.
5. Run `python manage.py collectstatic` and serve `/static/` via your web
   server or a CDN (Nginx, WhiteNoise, etc.).
6. Run the app behind a real WSGI server: `gunicorn config.wsgi:application`.
7. Put a reverse proxy (Nginx) in front for TLS termination, and set
   `SECURE_SSL_REDIRECT=True`.
8. Schedule `cleanup_expired_files` as shown above.
9. For heavier load, move PDF processing into a background worker (Celery +
   Redis/RabbitMQ) - the service layer in `pdf_tools/services/` is already
   decoupled from the view/request cycle, so this is a drop-in change: call
   the same service functions from a Celery task instead of inline in the
   view.

## Project structure

```
pdfino/
    manage.py
    requirements.txt
    README.md
    .gitignore
    .env.example

    config/
        settings.py
        urls.py
        wsgi.py
        asgi.py

    pdf_tools/
        models.py            # ConversionJob (metadata only, no file contents)
        views.py             # One view per tool
        urls.py
        forms.py
        tool_registry.py     # Single source of truth for every tool's metadata
        middleware.py         # Turns unexpected errors into a friendly page
        context_processors.py
        services/            # Real PDF processing logic, framework-agnostic
            conversion.py
            organization.py
            optimization.py
            security.py
            editing.py
            exceptions.py
        utils/
            files.py          # Safe upload validation & handling
            pages.py          # "1-3, 5, 7-10" page-range parsing
        management/commands/
            cleanup_expired_files.py
        templates/
        static/
            css/main.css
            js/main.js
        tests/
            test_core.py
```

Processing logic lives in `pdf_tools/services/`, never inline in `views.py` -
this keeps views thin and makes the processing logic independently testable
and reusable (e.g. from a future Celery task or management command).

## Security notes

- Uploaded files are validated by their real content (magic bytes), not just
  their extension.
- All files written to disk use randomly generated names inside per-request
  directories - user-supplied filenames are never used to build a filesystem
  path, which prevents path traversal.
- Passwords for the Protect PDF tool are never logged.
- Unexpected errors never leak a Python traceback to the browser in
  production (`DEBUG=False`); they're logged to `logs/pdfino.log` instead.
- CSRF protection is enabled Django-wide, including on every upload form.
