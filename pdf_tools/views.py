import io
import logging
import zipfile
from pathlib import Path

import fitz
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth import login as auth_login
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import redirect, render
from django.core import signing
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from . import forms
from .models import ConversionJob
from .services import conversion, editing, organization, optimization, security as sec
from .services.exceptions import ProcessingError
from .tool_registry import TOOLS, TOOLS_BY_SLUG, tools_by_category
from .utils import files as file_utils
from .utils import staging
from .utils.pages import PageRangeError, parse_page_ranges

logger = logging.getLogger('pdf_tools')


# ---------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------

def _client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    return xff.split(',')[0].strip() if xff else request.META.get('REMOTE_ADDR')


def _start_job(request, slug):
    tool = TOOLS_BY_SLUG[slug]
    return ConversionJob.objects.create(
        tool_slug=slug,
        tool_name=tool['name'],
        category=tool['category'],
        status=ConversionJob.Status.PROCESSING,
        user=request.user if request.user.is_authenticated else None,
        ip_address=_client_ip(request),
    )


def _finish_job(job, *, result_path: Path | None, original_size=0, original_filename='', error=None):
    job.completed_at = timezone.now()
    if original_filename:
        job.original_filename = original_filename[:255]
    if error:
        job.status = ConversionJob.Status.FAILED
        job.error_message = str(error)[:500]
    else:
        try:
            path = _validate_output(result_path, job.tool_slug)
        except ProcessingError as exc:
            if result_path:
                root = Path(settings.OUTPUT_TMP_DIR).resolve()
                candidate_parent = Path(result_path).resolve().parent
                if root in candidate_parent.parents and candidate_parent != root:
                    file_utils.cleanup_dir(candidate_parent)
            job.status = ConversionJob.Status.FAILED
            job.error_message = str(exc)[:500]
            job.save()
            raise
        job.status = ConversionJob.Status.SUCCESS
        job.original_size_bytes = original_size
        job.result_filename = path.name
        job.result_size_bytes = path.stat().st_size
        job.result_relpath = str(path.relative_to(Path(settings.OUTPUT_TMP_DIR).resolve()))
        from datetime import timedelta
        job.expires_at = timezone.now() + timedelta(minutes=settings.FILE_RETENTION_MINUTES)
        if job.user_id is None:
            job.download_token = signing.TimestampSigner(salt='pdfino-download').sign(str(job.id))
    job.save()


def _validate_output(result_path, tool_slug):
    if result_path is None:
        raise ProcessingError('The conversion did not produce a result file.')
    root = Path(settings.OUTPUT_TMP_DIR).resolve()
    path = Path(result_path).resolve()
    if root not in path.parents or not path.is_file():
        raise ProcessingError('The conversion produced an invalid result.')
    size = path.stat().st_size
    if size == 0 or size > settings.MAX_OUTPUT_SIZE:
        raise ProcessingError('The result file is empty or exceeds the configured size limit.')
    expected = path.suffix.lower()
    header = path.read_bytes()[:8]
    valid = {
        '.pdf': header.startswith(b'%PDF-'),
        '.docx': header.startswith(b'PK'),
        '.zip': header.startswith(b'PK'),
        '.jpg': header.startswith(b'\xff\xd8\xff'),
        '.jpeg': header.startswith(b'\xff\xd8\xff'),
        '.png': header.startswith(b'\x89PNG\r\n\x1a\n'),
        '.txt': True,
    }.get(expected, False)
    if not valid:
        raise ProcessingError('The conversion produced an unexpected file type.')
    if expected == '.pdf':
        try:
            doc = fitz.open(path)
            pages = doc.page_count
            doc.close()
        except Exception as exc:
            raise ProcessingError('The conversion produced an invalid PDF.') from exc
        if pages == 0 or pages > settings.MAX_OUTPUT_PAGES:
            raise ProcessingError('The conversion produced too many output pages.')
    return path


def _tool_context(slug, **extra):
    tool = TOOLS_BY_SLUG.get(slug)
    if not tool:
        raise Http404('Unknown tool')
    ctx = {'tool': tool, 'slug': slug}
    ctx.update(extra)
    return ctx


def _render_tool(request, slug, template_name, extra=None, status=200):
    return render(request, template_name, _tool_context(slug, **(extra or {})), status=status)


def _serve_download(request, job_id, filename):
    job = _get_job_or_404(job_id)
    if job.user_id is not None:
        if not request.user.is_authenticated or request.user.id != job.user_id:
            raise Http404('File not found.')
    else:
        token = request.GET.get('token', '')
        try:
            token_job_id = signing.TimestampSigner(salt='pdfino-download').unsign(
                token, max_age=settings.DOWNLOAD_TOKEN_MAX_AGE
            )
        except signing.BadSignature:
            raise Http404('File not found.')
        if token_job_id != str(job.id):
            raise Http404('File not found.')
    if job.status != ConversionJob.Status.SUCCESS or not job.result_relpath:
        raise Http404('This file is not available (it may have expired).')
    if job.expires_at and job.expires_at <= timezone.now():
        raise Http404('This file has expired and was automatically removed. Please process it again.')
    path = Path(settings.OUTPUT_TMP_DIR) / job.result_relpath
    try:
        path = path.resolve()
        path.relative_to(Path(settings.OUTPUT_TMP_DIR).resolve())
    except ValueError:
        raise Http404('File not found.')
    if not path.is_file():
        raise Http404('This file has expired and was automatically removed. Please process it again.')
    response = FileResponse(open(path, 'rb'), as_attachment=True, filename=filename or path.name)
    return response


def _get_job_or_404(job_id):
    try:
        return ConversionJob.objects.get(id=job_id)
    except (ConversionJob.DoesNotExist, ValueError, TypeError):
        raise Http404('Job not found.')


def zip_files(paths, zip_path: Path, arc_names=None):
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for i, p in enumerate(paths):
            name = arc_names[i] if arc_names else Path(p).name
            zf.write(p, arcname=name)
    return zip_path


# ---------------------------------------------------------------------
# Static / informational pages
# ---------------------------------------------------------------------

def home(request):
    return render(request, 'pdf_tools/home.html', {'categories': tools_by_category(), 'tools': TOOLS})


def about(request):
    return render(request, 'pdf_tools/about.html')


def privacy(request):
    return render(request, 'pdf_tools/privacy.html')


def terms(request):
    return render(request, 'pdf_tools/terms.html')


def faq(request):
    return render(request, 'pdf_tools/faq.html')


def error_404(request, exception=None):
    return render(request, 'pdf_tools/404.html', status=404)


def error_500(request):
    return render(request, 'pdf_tools/500.html', status=500)


def download(request, job_id):
    job = _get_job_or_404(job_id)
    return _serve_download(request, job_id, job.result_filename)


def signup(request):
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            auth_login(request, user)
            messages.success(request, f'Welcome to Pdfino, {user.username}!')
            return redirect('dashboard')
    else:
        form = UserCreationForm()
    for field in form.fields.values():
        field.widget.attrs['class'] = 'form-control'
    return render(request, 'registration/signup.html', {'form': form})


@login_required
def dashboard(request):
    jobs = request.user.jobs.all()[:50]
    return render(request, 'pdf_tools/dashboard.html', {'jobs': jobs})


# ---------------------------------------------------------------------
# Merge PDF
# ---------------------------------------------------------------------

@require_http_methods(['GET', 'POST'])
def merge_pdf(request):
    slug = 'merge-pdf'
    if request.method == 'GET':
        return _render_tool(request, slug, 'pdf_tools/tool_merge.html')

    uploaded = request.FILES.getlist('files')
    if len(uploaded) < 2:
        return _render_tool(request, slug, 'pdf_tools/tool_merge.html',
                             {'error': 'Please upload at least two PDF files.'}, status=400)
    if len(uploaded) > settings.MAX_UPLOAD_FILES:
        return _render_tool(request, slug, 'pdf_tools/tool_merge.html',
                             {'error': f'You can merge up to {settings.MAX_UPLOAD_FILES} files at a time.'}, status=400)
    if sum(f.size for f in uploaded) > settings.MAX_TOTAL_UPLOAD_SIZE:
        return _render_tool(request, slug, 'pdf_tools/tool_merge.html',
                             {'error': 'The combined upload size is too large.'}, status=400)

    job = _start_job(request, slug)
    upload_dir = file_utils.new_upload_dir()
    output_dir = file_utils.new_output_dir()
    total_size = 0
    try:
        saved_paths = []
        for f in uploaded:
            file_utils.validate_pdf_upload(f)
            total_size += f.size
            saved_paths.append(file_utils.save_upload(f, upload_dir, 'pdf'))

        out_path = output_dir / 'Pdfino_merged.pdf'
        result = organization.merge_pdfs(saved_paths, out_path)
        _finish_job(job, result_path=out_path, original_size=total_size)
        return _render_tool(request, slug, 'pdf_tools/tool_merge.html', {
            'success': True, 'job': job, 'result': result,
        })
    except (ProcessingError, file_utils.UnsafeFileError) as exc:
        _finish_job(job, result_path=None, error=exc)
        file_utils.cleanup_dir(output_dir)
        return _render_tool(request, slug, 'pdf_tools/tool_merge.html', {'error': str(exc)}, status=400)
    except Exception as exc:
        logger.exception('Unexpected error in merge_pdf')
        _finish_job(job, result_path=None, error='Unexpected error')
        file_utils.cleanup_dir(output_dir)
        return _render_tool(request, slug, 'pdf_tools/tool_merge.html',
                             {'error': 'Something went wrong while merging your files. Please try again.'}, status=500)
    finally:
        file_utils.cleanup_dir(upload_dir)


# ---------------------------------------------------------------------
# Split PDF
# ---------------------------------------------------------------------

@require_http_methods(['GET', 'POST'])
def split_pdf(request):
    slug = 'split-pdf'
    if request.method == 'GET':
        return _render_tool(request, slug, 'pdf_tools/tool_split.html', {'form': forms.SplitForm()})

    form = forms.SplitForm(request.POST)
    upload = request.FILES.get('file')
    if not upload or not form.is_valid():
        return _render_tool(request, slug, 'pdf_tools/tool_split.html',
                             {'form': form, 'error': 'Please upload a PDF and check your options.'}, status=400)

    job = _start_job(request, slug)
    upload_dir = file_utils.new_upload_dir()
    output_dir = file_utils.new_output_dir()
    try:
        file_utils.validate_pdf_upload(upload)
        pdf_path = file_utils.save_upload(upload, upload_dir, 'pdf')
        page_count = organization.get_page_count(pdf_path)

        mode = form.cleaned_data['mode']
        if mode == 'every_page':
            parts = organization.split_every_page(pdf_path, output_dir)
        else:
            ranges_text = form.cleaned_data['ranges']
            groups = [g.strip() for g in ranges_text.split(';') if g.strip()] or [ranges_text]
            # Support either one combined range list (one output file) or
            # semicolon-separated groups (one output file per group).
            if len(groups) <= 1:
                indices = parse_page_ranges(ranges_text, page_count)
                parts = organization.split_by_ranges(pdf_path, [indices], output_dir)
            else:
                ranges = [parse_page_ranges(g, page_count) for g in groups]
                parts = organization.split_by_ranges(pdf_path, ranges, output_dir)

        if len(parts) == 1:
            out_path = parts[0]
        else:
            out_path = output_dir / 'Pdfino_split.zip'
            zip_files(parts, out_path)

        _finish_job(job, result_path=out_path, original_size=upload.size, original_filename=upload.name)
        return _render_tool(request, slug, 'pdf_tools/tool_split.html', {
            'success': True, 'job': job, 'file_count': len(parts),
        })
    except (ProcessingError, file_utils.UnsafeFileError, PageRangeError) as exc:
        _finish_job(job, result_path=None, error=exc)
        file_utils.cleanup_dir(output_dir)
        return _render_tool(request, slug, 'pdf_tools/tool_split.html', {'form': form, 'error': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error in split_pdf')
        _finish_job(job, result_path=None, error='Unexpected error')
        file_utils.cleanup_dir(output_dir)
        return _render_tool(request, slug, 'pdf_tools/tool_split.html',
                             {'form': form, 'error': 'Something went wrong while splitting your PDF.'}, status=500)
    finally:
        file_utils.cleanup_dir(upload_dir)


# ---------------------------------------------------------------------
# Rotate PDF
# ---------------------------------------------------------------------

@require_http_methods(['GET', 'POST'])
def rotate_pdf(request):
    slug = 'rotate-pdf'
    if request.method == 'GET':
        return _render_tool(request, slug, 'pdf_tools/tool_rotate.html', {'form': forms.RotateForm()})

    form = forms.RotateForm(request.POST)
    upload = request.FILES.get('file')
    if not upload or not form.is_valid():
        return _render_tool(request, slug, 'pdf_tools/tool_rotate.html',
                             {'form': form, 'error': 'Please upload a PDF and check your options.'}, status=400)

    job = _start_job(request, slug)
    upload_dir = file_utils.new_upload_dir()
    output_dir = file_utils.new_output_dir()
    try:
        file_utils.validate_pdf_upload(upload)
        pdf_path = file_utils.save_upload(upload, upload_dir, 'pdf')
        page_count = organization.get_page_count(pdf_path)

        indices = None
        if form.cleaned_data['scope'] == 'selected':
            indices = parse_page_ranges(form.cleaned_data['pages'], page_count)

        out_path = output_dir / 'Pdfino_rotated.pdf'
        result = organization.rotate_pdf(pdf_path, out_path, int(form.cleaned_data['degrees']), indices)
        _finish_job(job, result_path=out_path, original_size=upload.size, original_filename=upload.name)
        return _render_tool(request, slug, 'pdf_tools/tool_rotate.html', {'success': True, 'job': job, 'result': result})
    except (ProcessingError, file_utils.UnsafeFileError, PageRangeError) as exc:
        _finish_job(job, result_path=None, error=exc)
        file_utils.cleanup_dir(output_dir)
        return _render_tool(request, slug, 'pdf_tools/tool_rotate.html', {'form': form, 'error': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error in rotate_pdf')
        _finish_job(job, result_path=None, error='Unexpected error')
        file_utils.cleanup_dir(output_dir)
        return _render_tool(request, slug, 'pdf_tools/tool_rotate.html',
                             {'form': form, 'error': 'Something went wrong while rotating your PDF.'}, status=500)
    finally:
        file_utils.cleanup_dir(upload_dir)


# ---------------------------------------------------------------------
# Delete / Extract pages (share a template + page-selection form)
# ---------------------------------------------------------------------

def _page_op_view(request, slug, template, op_func, success_extra=lambda r: {}):
    if request.method == 'GET':
        return _render_tool(request, slug, template, {'form': forms.PageSelectionForm()})

    form = forms.PageSelectionForm(request.POST)
    upload = request.FILES.get('file')
    if not upload or not form.is_valid():
        return _render_tool(request, slug, template,
                             {'form': form, 'error': 'Please upload a PDF and enter page numbers.'}, status=400)

    job = _start_job(request, slug)
    upload_dir = file_utils.new_upload_dir()
    output_dir = file_utils.new_output_dir()
    try:
        file_utils.validate_pdf_upload(upload)
        pdf_path = file_utils.save_upload(upload, upload_dir, 'pdf')
        page_count = organization.get_page_count(pdf_path)
        indices = parse_page_ranges(form.cleaned_data['pages'], page_count)

        out_path = output_dir / f'Pdfino_{slug.replace("-", "_")}.pdf'
        result = op_func(pdf_path, out_path, indices)
        _finish_job(job, result_path=out_path, original_size=upload.size, original_filename=upload.name)
        ctx = {'success': True, 'job': job, 'result': result}
        ctx.update(success_extra(result))
        return _render_tool(request, slug, template, ctx)
    except (ProcessingError, file_utils.UnsafeFileError, PageRangeError) as exc:
        _finish_job(job, result_path=None, error=exc)
        file_utils.cleanup_dir(output_dir)
        return _render_tool(request, slug, template, {'form': form, 'error': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error in %s', slug)
        _finish_job(job, result_path=None, error='Unexpected error')
        file_utils.cleanup_dir(output_dir)
        return _render_tool(request, slug, template,
                             {'form': form, 'error': 'Something went wrong while processing your PDF.'}, status=500)
    finally:
        file_utils.cleanup_dir(upload_dir)


@require_http_methods(['GET', 'POST'])
def delete_pages(request):
    return _page_op_view(request, 'delete-pages', 'pdf_tools/tool_delete_pages.html', organization.delete_pages)


@require_http_methods(['GET', 'POST'])
def extract_pages(request):
    return _page_op_view(request, 'extract-pages', 'pdf_tools/tool_extract_pages.html', organization.extract_pages)


# ---------------------------------------------------------------------
# Reorder pages
# ---------------------------------------------------------------------

@require_http_methods(['GET', 'POST'])
def reorder_pages(request):
    """
    Two-stage flow: first upload is 'inspected' to get the page count (so the
    UI can show one draggable tile per page), then the chosen order is applied.
    The original bytes are round-tripped via a base64 hidden field so the user
    only has to upload once.
    """
    slug = 'reorder-pages'
    if request.method == 'GET':
        return _render_tool(request, slug, 'pdf_tools/tool_reorder_pages.html')

    stage = request.POST.get('stage', 'inspect')

    if stage == 'inspect':
        upload = request.FILES.get('file')
        if not upload:
            return _render_tool(request, slug, 'pdf_tools/tool_reorder_pages.html',
                                 {'error': 'Please upload a PDF.'}, status=400)
        try:
            staged = staging.stage_upload(request, upload)
            pdf_path = Path(settings.STAGING_TMP_DIR) / staged.relpath
            page_count = organization.get_page_count(pdf_path)
            return _render_tool(request, slug, 'pdf_tools/tool_reorder_pages.html', {
                'inspected': True, 'page_count': page_count,
                'staging_id': staged.token, 'file_name': staged.original_filename,
            })
        except (ProcessingError, file_utils.UnsafeFileError) as exc:
            return _render_tool(request, slug, 'pdf_tools/tool_reorder_pages.html', {'error': str(exc)}, status=400)

    # stage == 'reorder'
    order_raw = request.POST.get('order', '')
    staging_id = request.POST.get('staging_id', '')
    staged_data = staging.get_staged(request, staging_id)
    if not order_raw or not staged_data:
        return _render_tool(request, slug, 'pdf_tools/tool_reorder_pages.html',
                             {'error': 'Please arrange every page before saving.'}, status=400)

    job = _start_job(request, slug)
    output_dir = file_utils.new_output_dir()
    staged, pdf_path = staged_data
    try:
        try:
            new_order = [int(x) for x in order_raw.split(',') if x.strip() != '']
        except ValueError:
            raise ProcessingError('Invalid page order submitted.')

        out_path = output_dir / 'Pdfino_reordered.pdf'
        result = organization.reorder_pages(pdf_path, out_path, new_order)
        _finish_job(job, result_path=out_path, original_size=staged.size_bytes, original_filename=staged.original_filename)
        staging.delete_staged(staged)
        return _render_tool(request, slug, 'pdf_tools/tool_reorder_pages.html', {'success': True, 'job': job, 'result': result})
    except (ProcessingError, file_utils.UnsafeFileError) as exc:
        _finish_job(job, result_path=None, error=exc)
        file_utils.cleanup_dir(output_dir)
        return _render_tool(request, slug, 'pdf_tools/tool_reorder_pages.html', {'error': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error in reorder_pages')
        _finish_job(job, result_path=None, error='Unexpected error')
        file_utils.cleanup_dir(output_dir)
        return _render_tool(request, slug, 'pdf_tools/tool_reorder_pages.html',
                             {'error': 'Something went wrong while reordering your PDF.'}, status=500)
    finally:
        pass


# ---------------------------------------------------------------------
# Compress PDF
# ---------------------------------------------------------------------

@require_http_methods(['GET', 'POST'])
def compress_pdf(request):
    slug = 'compress-pdf'
    if request.method == 'GET':
        return _render_tool(request, slug, 'pdf_tools/tool_compress.html', {'form': forms.CompressForm()})

    form = forms.CompressForm(request.POST)
    upload = request.FILES.get('file')
    if not upload or not form.is_valid():
        return _render_tool(request, slug, 'pdf_tools/tool_compress.html',
                             {'form': form, 'error': 'Please upload a PDF.'}, status=400)

    job = _start_job(request, slug)
    upload_dir = file_utils.new_upload_dir()
    output_dir = file_utils.new_output_dir()
    try:
        file_utils.validate_pdf_upload(upload)
        pdf_path = file_utils.save_upload(upload, upload_dir, 'pdf')
        out_path = output_dir / 'Pdfino_compressed.pdf'
        result = optimization.compress_pdf(pdf_path, out_path, form.cleaned_data['level'])
        _finish_job(job, result_path=out_path, original_size=result['original_size'])
        reduction = round((1 - result['compressed_size'] / result['original_size']) * 100, 1) if result['original_size'] else 0
        return _render_tool(request, slug, 'pdf_tools/tool_compress.html', {
            'success': True, 'job': job, 'result': result, 'reduction': reduction,
        })
    except (ProcessingError, file_utils.UnsafeFileError) as exc:
        _finish_job(job, result_path=None, error=exc)
        file_utils.cleanup_dir(output_dir)
        return _render_tool(request, slug, 'pdf_tools/tool_compress.html', {'form': form, 'error': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error in compress_pdf')
        _finish_job(job, result_path=None, error='Unexpected error')
        file_utils.cleanup_dir(output_dir)
        return _render_tool(request, slug, 'pdf_tools/tool_compress.html',
                             {'form': form, 'error': 'Something went wrong while compressing your PDF.'}, status=500)
    finally:
        file_utils.cleanup_dir(upload_dir)


# ---------------------------------------------------------------------
# Watermark / Page numbers
# ---------------------------------------------------------------------

@require_http_methods(['GET', 'POST'])
def watermark_pdf(request):
    slug = 'watermark-pdf'
    if request.method == 'GET':
        return _render_tool(request, slug, 'pdf_tools/tool_watermark.html', {'form': forms.WatermarkForm()})

    form = forms.WatermarkForm(request.POST)
    upload = request.FILES.get('file')
    if not upload or not form.is_valid():
        return _render_tool(request, slug, 'pdf_tools/tool_watermark.html',
                             {'form': form, 'error': 'Please upload a PDF and check your options.'}, status=400)

    job = _start_job(request, slug)
    upload_dir = file_utils.new_upload_dir()
    output_dir = file_utils.new_output_dir()
    try:
        file_utils.validate_pdf_upload(upload)
        pdf_path = file_utils.save_upload(upload, upload_dir, 'pdf')
        page_count = organization.get_page_count(pdf_path)
        indices = None
        if form.cleaned_data['scope'] == 'selected':
            indices = parse_page_ranges(form.cleaned_data['pages'], page_count)

        out_path = output_dir / 'Pdfino_watermarked.pdf'
        result = editing.watermark_pdf(
            pdf_path, out_path, form.cleaned_data['text'],
            font_size=form.cleaned_data['font_size'], opacity=form.cleaned_data['opacity'],
            rotation=form.cleaned_data['rotation'], position=form.cleaned_data['position'],
            page_indices=indices,
        )
        _finish_job(job, result_path=out_path, original_size=upload.size, original_filename=upload.name)
        return _render_tool(request, slug, 'pdf_tools/tool_watermark.html', {'success': True, 'job': job, 'result': result})
    except (ProcessingError, file_utils.UnsafeFileError, PageRangeError) as exc:
        _finish_job(job, result_path=None, error=exc)
        file_utils.cleanup_dir(output_dir)
        return _render_tool(request, slug, 'pdf_tools/tool_watermark.html', {'form': form, 'error': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error in watermark_pdf')
        _finish_job(job, result_path=None, error='Unexpected error')
        file_utils.cleanup_dir(output_dir)
        return _render_tool(request, slug, 'pdf_tools/tool_watermark.html',
                             {'form': form, 'error': 'Something went wrong while adding the watermark.'}, status=500)
    finally:
        file_utils.cleanup_dir(upload_dir)


@require_http_methods(['GET', 'POST'])
def page_numbers(request):
    slug = 'page-numbers'
    if request.method == 'GET':
        return _render_tool(request, slug, 'pdf_tools/tool_page_numbers.html', {'form': forms.PageNumbersForm()})

    form = forms.PageNumbersForm(request.POST)
    upload = request.FILES.get('file')
    if not upload or not form.is_valid():
        return _render_tool(request, slug, 'pdf_tools/tool_page_numbers.html',
                             {'form': form, 'error': 'Please upload a PDF.'}, status=400)

    job = _start_job(request, slug)
    upload_dir = file_utils.new_upload_dir()
    output_dir = file_utils.new_output_dir()
    try:
        file_utils.validate_pdf_upload(upload)
        pdf_path = file_utils.save_upload(upload, upload_dir, 'pdf')
        out_path = output_dir / 'Pdfino_numbered.pdf'
        result = editing.add_page_numbers(
            pdf_path, out_path, position=form.cleaned_data['position'], start_at=form.cleaned_data['start_at'],
        )
        _finish_job(job, result_path=out_path, original_size=upload.size, original_filename=upload.name)
        return _render_tool(request, slug, 'pdf_tools/tool_page_numbers.html', {'success': True, 'job': job, 'result': result})
    except (ProcessingError, file_utils.UnsafeFileError) as exc:
        _finish_job(job, result_path=None, error=exc)
        file_utils.cleanup_dir(output_dir)
        return _render_tool(request, slug, 'pdf_tools/tool_page_numbers.html', {'form': form, 'error': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error in page_numbers')
        _finish_job(job, result_path=None, error='Unexpected error')
        file_utils.cleanup_dir(output_dir)
        return _render_tool(request, slug, 'pdf_tools/tool_page_numbers.html',
                             {'form': form, 'error': 'Something went wrong while numbering the pages.'}, status=500)
    finally:
        file_utils.cleanup_dir(upload_dir)


# ---------------------------------------------------------------------
# Protect / Unlock
# ---------------------------------------------------------------------

@require_http_methods(['GET', 'POST'])
def protect_pdf(request):
    slug = 'protect-pdf'
    if request.method == 'GET':
        return _render_tool(request, slug, 'pdf_tools/tool_protect.html', {'form': forms.ProtectForm()})

    form = forms.ProtectForm(request.POST)
    upload = request.FILES.get('file')
    if not upload or not form.is_valid():
        return _render_tool(request, slug, 'pdf_tools/tool_protect.html',
                             {'form': form, 'error': 'Please upload a PDF and fix the errors below.'}, status=400)

    job = _start_job(request, slug)
    upload_dir = file_utils.new_upload_dir()
    output_dir = file_utils.new_output_dir()
    try:
        file_utils.validate_pdf_upload(upload)
        pdf_path = file_utils.save_upload(upload, upload_dir, 'pdf')
        out_path = output_dir / 'Pdfino_protected.pdf'
        sec.protect_pdf(pdf_path, out_path, form.cleaned_data['password'])
        _finish_job(job, result_path=out_path, original_size=upload.size, original_filename=upload.name)
        return _render_tool(request, slug, 'pdf_tools/tool_protect.html', {'success': True, 'job': job})
    except (ProcessingError, file_utils.UnsafeFileError) as exc:
        _finish_job(job, result_path=None, error=exc)
        file_utils.cleanup_dir(output_dir)
        return _render_tool(request, slug, 'pdf_tools/tool_protect.html', {'form': form, 'error': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error in protect_pdf')
        _finish_job(job, result_path=None, error='Unexpected error')
        file_utils.cleanup_dir(output_dir)
        return _render_tool(request, slug, 'pdf_tools/tool_protect.html',
                             {'form': form, 'error': 'Something went wrong while protecting your PDF.'}, status=500)
    finally:
        file_utils.cleanup_dir(upload_dir)


@require_http_methods(['GET', 'POST'])
def unlock_pdf(request):
    slug = 'unlock-pdf'
    if request.method == 'GET':
        return _render_tool(request, slug, 'pdf_tools/tool_unlock.html', {'form': forms.UnlockForm()})

    form = forms.UnlockForm(request.POST)
    upload = request.FILES.get('file')
    if not upload or not form.is_valid():
        return _render_tool(request, slug, 'pdf_tools/tool_unlock.html',
                             {'form': form, 'error': 'Please upload a PDF and enter its password.'}, status=400)

    job = _start_job(request, slug)
    upload_dir = file_utils.new_upload_dir()
    output_dir = file_utils.new_output_dir()
    try:
        file_utils.validate_pdf_upload(upload)
        pdf_path = file_utils.save_upload(upload, upload_dir, 'pdf')
        out_path = output_dir / 'Pdfino_unlocked.pdf'
        sec.unlock_pdf(pdf_path, out_path, form.cleaned_data['password'])
        _finish_job(job, result_path=out_path, original_size=upload.size, original_filename=upload.name)
        return _render_tool(request, slug, 'pdf_tools/tool_unlock.html', {'success': True, 'job': job})
    except (ProcessingError, file_utils.UnsafeFileError) as exc:
        _finish_job(job, result_path=None, error=exc)
        file_utils.cleanup_dir(output_dir)
        return _render_tool(request, slug, 'pdf_tools/tool_unlock.html', {'form': form, 'error': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error in unlock_pdf')
        _finish_job(job, result_path=None, error='Unexpected error')
        file_utils.cleanup_dir(output_dir)
        return _render_tool(request, slug, 'pdf_tools/tool_unlock.html',
                             {'form': form, 'error': 'Something went wrong while unlocking your PDF.'}, status=500)
    finally:
        file_utils.cleanup_dir(upload_dir)


# ---------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------

@require_http_methods(['GET', 'POST'])
def edit_metadata(request):
    slug = 'edit-metadata'
    if request.method == 'GET':
        return _render_tool(request, slug, 'pdf_tools/tool_metadata.html', {'form': forms.MetadataForm()})

    upload = request.FILES.get('file')
    stage = request.POST.get('stage', 'inspect')

    if stage == 'inspect':
        if not upload:
            return _render_tool(request, slug, 'pdf_tools/tool_metadata.html', {'form': forms.MetadataForm(), 'error': 'Please upload a PDF.'}, status=400)
        try:
            staged = staging.stage_upload(request, upload)
            pdf_path = Path(settings.STAGING_TMP_DIR) / staged.relpath
            meta = sec.get_metadata(pdf_path)
            form = forms.MetadataForm(initial=meta)
            return _render_tool(request, slug, 'pdf_tools/tool_metadata.html', {
                'form': form, 'inspected': True, 'staging_id': staged.token,
                'file_name': staged.original_filename,
            })
        except (ProcessingError, file_utils.UnsafeFileError) as exc:
            return _render_tool(request, slug, 'pdf_tools/tool_metadata.html', {'form': forms.MetadataForm(), 'error': str(exc)}, status=400)

    # stage == 'save'
    form = forms.MetadataForm(request.POST)
    staging_id = request.POST.get('staging_id', '')
    staged_data = staging.get_staged(request, staging_id)
    if not form.is_valid() or not staged_data:
        return _render_tool(request, slug, 'pdf_tools/tool_metadata.html', {'form': form, 'error': 'Please fix the errors below.'}, status=400)

    job = _start_job(request, slug)
    output_dir = file_utils.new_output_dir()
    staged, pdf_path = staged_data
    try:
        out_path = output_dir / 'Pdfino_metadata.pdf'
        sec.set_metadata(pdf_path, out_path, form.cleaned_data)
        _finish_job(job, result_path=out_path, original_size=staged.size_bytes, original_filename=staged.original_filename)
        staging.delete_staged(staged)
        return _render_tool(request, slug, 'pdf_tools/tool_metadata.html', {'success': True, 'job': job})
    except ProcessingError as exc:
        _finish_job(job, result_path=None, error=exc)
        file_utils.cleanup_dir(output_dir)
        return _render_tool(request, slug, 'pdf_tools/tool_metadata.html', {'form': form, 'error': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error in edit_metadata save stage')
        _finish_job(job, result_path=None, error='Unexpected error')
        file_utils.cleanup_dir(output_dir)
        return _render_tool(request, slug, 'pdf_tools/tool_metadata.html',
                             {'form': form, 'error': 'Something went wrong while saving metadata.'}, status=500)
    finally:
        pass


# ---------------------------------------------------------------------
# PDF info / preview
# ---------------------------------------------------------------------

@require_http_methods(['GET', 'POST'])
def pdf_info(request):
    slug = 'pdf-info'
    if request.method == 'GET':
        return _render_tool(request, slug, 'pdf_tools/tool_info.html')

    upload = request.FILES.get('file')
    if not upload:
        return _render_tool(request, slug, 'pdf_tools/tool_info.html', {'error': 'Please upload a PDF.'}, status=400)

    upload_dir = file_utils.new_upload_dir()
    try:
        file_utils.validate_pdf_upload(upload)
        pdf_path = file_utils.save_upload(upload, upload_dir, 'pdf')
        info = editing.get_pdf_info(pdf_path)
        info['file_size'] = file_utils.human_size(upload.size)
        info['file_name'] = upload.name
        return _render_tool(request, slug, 'pdf_tools/tool_info.html', {'info': info})
    except (ProcessingError, file_utils.UnsafeFileError) as exc:
        return _render_tool(request, slug, 'pdf_tools/tool_info.html', {'error': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error in pdf_info')
        return _render_tool(request, slug, 'pdf_tools/tool_info.html',
                             {'error': 'Could not read this PDF.'}, status=500)
    finally:
        file_utils.cleanup_dir(upload_dir)


# ---------------------------------------------------------------------
# PDF <-> Word / Text
# ---------------------------------------------------------------------

@require_http_methods(['GET', 'POST'])
def pdf_to_word(request):
    slug = 'pdf-to-word'
    if request.method == 'GET':
        return _render_tool(request, slug, 'pdf_tools/tool_pdf_to_word.html')

    upload = request.FILES.get('file')
    if not upload:
        return _render_tool(request, slug, 'pdf_tools/tool_pdf_to_word.html', {'error': 'Please upload a PDF.'}, status=400)

    job = _start_job(request, slug)
    upload_dir = file_utils.new_upload_dir()
    output_dir = file_utils.new_output_dir()
    try:
        file_utils.validate_pdf_upload(upload)
        pdf_path = file_utils.save_upload(upload, upload_dir, 'pdf')
        out_path = output_dir / 'Pdfino_converted.docx'
        result = conversion.pdf_to_word(pdf_path, out_path)
        _finish_job(job, result_path=out_path, original_size=upload.size, original_filename=upload.name)
        return _render_tool(request, slug, 'pdf_tools/tool_pdf_to_word.html', {'success': True, 'job': job, 'result': result})
    except (ProcessingError, file_utils.UnsafeFileError) as exc:
        _finish_job(job, result_path=None, error=exc)
        file_utils.cleanup_dir(output_dir)
        return _render_tool(request, slug, 'pdf_tools/tool_pdf_to_word.html', {'error': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error in pdf_to_word')
        _finish_job(job, result_path=None, error='Unexpected error')
        file_utils.cleanup_dir(output_dir)
        return _render_tool(request, slug, 'pdf_tools/tool_pdf_to_word.html',
                             {'error': 'This PDF could not be converted to Word.'}, status=500)
    finally:
        file_utils.cleanup_dir(upload_dir)


@require_http_methods(['GET', 'POST'])
def pdf_to_text(request):
    slug = 'pdf-to-text'
    if request.method == 'GET':
        return _render_tool(request, slug, 'pdf_tools/tool_pdf_to_text.html')

    upload = request.FILES.get('file')
    if not upload:
        return _render_tool(request, slug, 'pdf_tools/tool_pdf_to_text.html', {'error': 'Please upload a PDF.'}, status=400)

    job = _start_job(request, slug)
    upload_dir = file_utils.new_upload_dir()
    output_dir = file_utils.new_output_dir()
    try:
        file_utils.validate_pdf_upload(upload)
        pdf_path = file_utils.save_upload(upload, upload_dir, 'pdf')
        out_path = output_dir / 'Pdfino_extracted.txt'
        result = conversion.pdf_to_text(pdf_path, out_path)
        _finish_job(job, result_path=out_path, original_size=upload.size, original_filename=upload.name)
        return _render_tool(request, slug, 'pdf_tools/tool_pdf_to_text.html', {'success': True, 'job': job, 'result': result})
    except (ProcessingError, file_utils.UnsafeFileError) as exc:
        _finish_job(job, result_path=None, error=exc)
        file_utils.cleanup_dir(output_dir)
        return _render_tool(request, slug, 'pdf_tools/tool_pdf_to_text.html', {'error': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error in pdf_to_text')
        _finish_job(job, result_path=None, error='Unexpected error')
        file_utils.cleanup_dir(output_dir)
        return _render_tool(request, slug, 'pdf_tools/tool_pdf_to_text.html',
                             {'error': 'This PDF could not be converted to text.'}, status=500)
    finally:
        file_utils.cleanup_dir(upload_dir)


@require_http_methods(['GET', 'POST'])
def text_to_pdf(request):
    slug = 'text-to-pdf'
    if request.method == 'GET':
        return _render_tool(request, slug, 'pdf_tools/tool_text_to_pdf.html', {'form': forms.TextToPdfForm()})

    form = forms.TextToPdfForm(request.POST)
    if not form.is_valid():
        return _render_tool(request, slug, 'pdf_tools/tool_text_to_pdf.html', {'form': form, 'error': 'Please enter some text.'}, status=400)

    job = _start_job(request, slug)
    output_dir = file_utils.new_output_dir()
    try:
        out_path = output_dir / 'Pdfino_document.pdf'
        conversion.text_to_pdf(form.cleaned_data['text'], out_path, form.cleaned_data.get('title') or 'Document')
        _finish_job(job, result_path=out_path, original_size=len(form.cleaned_data['text'].encode('utf-8')))
        return _render_tool(request, slug, 'pdf_tools/tool_text_to_pdf.html', {'success': True, 'job': job})
    except ProcessingError as exc:
        _finish_job(job, result_path=None, error=exc)
        file_utils.cleanup_dir(output_dir)
        return _render_tool(request, slug, 'pdf_tools/tool_text_to_pdf.html', {'form': form, 'error': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error in text_to_pdf')
        _finish_job(job, result_path=None, error='Unexpected error')
        file_utils.cleanup_dir(output_dir)
        return _render_tool(request, slug, 'pdf_tools/tool_text_to_pdf.html',
                             {'form': form, 'error': 'Something went wrong while generating your PDF.'}, status=500)


# ---------------------------------------------------------------------
# PDF <-> Images
# ---------------------------------------------------------------------

def _pdf_to_image_view(request, slug, image_format, template):
    if request.method == 'GET':
        return _render_tool(request, slug, template, {'form': forms.PdfToImageOptionsForm()})

    form = forms.PdfToImageOptionsForm(request.POST)
    upload = request.FILES.get('file')
    if not upload or not form.is_valid():
        return _render_tool(request, slug, template, {'form': form, 'error': 'Please upload a PDF.'}, status=400)

    job = _start_job(request, slug)
    upload_dir = file_utils.new_upload_dir()
    output_dir = file_utils.new_output_dir()
    try:
        file_utils.validate_pdf_upload(upload)
        pdf_path = file_utils.save_upload(upload, upload_dir, 'pdf')
        page_count = organization.get_page_count(pdf_path)
        indices = None
        if form.cleaned_data['scope'] == 'selected':
            indices = parse_page_ranges(form.cleaned_data['pages'], page_count)

        images = conversion.pdf_to_images(pdf_path, output_dir, image_format=image_format,
                                           pages=indices, dpi=int(form.cleaned_data['quality']))
        if len(images) == 1:
            out_path = images[0]
        else:
            out_path = output_dir / f'Pdfino_images.zip'
            zip_files(images, out_path)

        _finish_job(job, result_path=out_path, original_size=upload.size, original_filename=upload.name)
        return _render_tool(request, slug, template, {'success': True, 'job': job, 'image_count': len(images)})
    except (ProcessingError, file_utils.UnsafeFileError, PageRangeError) as exc:
        _finish_job(job, result_path=None, error=exc)
        file_utils.cleanup_dir(output_dir)
        return _render_tool(request, slug, template, {'form': form, 'error': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error in %s', slug)
        _finish_job(job, result_path=None, error='Unexpected error')
        file_utils.cleanup_dir(output_dir)
        return _render_tool(request, slug, template,
                             {'form': form, 'error': 'Something went wrong while converting your PDF.'}, status=500)
    finally:
        file_utils.cleanup_dir(upload_dir)


@require_http_methods(['GET', 'POST'])
def pdf_to_jpg(request):
    return _pdf_to_image_view(request, 'pdf-to-jpg', 'jpg', 'pdf_tools/tool_pdf_to_image.html')


@require_http_methods(['GET', 'POST'])
def pdf_to_png(request):
    return _pdf_to_image_view(request, 'pdf-to-png', 'png', 'pdf_tools/tool_pdf_to_image.html')


def _image_to_pdf_view(request, slug, template):
    if request.method == 'GET':
        return _render_tool(request, slug, template, {'form': forms.ImageToPdfOptionsForm()})

    form = forms.ImageToPdfOptionsForm(request.POST)
    uploaded = request.FILES.getlist('files')
    if not uploaded or not form.is_valid():
        return _render_tool(request, slug, template, {'form': form, 'error': 'Please add at least one image.'}, status=400)
    if len(uploaded) > settings.MAX_UPLOAD_FILES:
        return _render_tool(request, slug, template, {
            'form': form, 'error': f'You can convert up to {settings.MAX_UPLOAD_FILES} images at once.'
        }, status=400)
    if sum(f.size for f in uploaded) > settings.MAX_TOTAL_UPLOAD_SIZE:
        return _render_tool(request, slug, template, {
            'form': form, 'error': 'The combined upload size is too large.'
        }, status=400)

    job = _start_job(request, slug)
    upload_dir = file_utils.new_upload_dir()
    output_dir = file_utils.new_output_dir()
    total_size = 0
    try:
        saved = []
        for f in uploaded:
            file_utils.validate_image_upload(f)
            total_size += f.size
            ext = 'png' if f.name.lower().endswith('.png') else 'jpg'
            saved.append(file_utils.save_upload(f, upload_dir, ext))

        out_path = output_dir / 'Pdfino_images.pdf'
        result = conversion.images_to_pdf(
            saved, out_path, page_size=form.cleaned_data['page_size'],
            orientation=form.cleaned_data['orientation'], margin_mm=form.cleaned_data['margin_mm'],
            fit=form.cleaned_data['fit'],
        )
        _finish_job(job, result_path=out_path, original_size=total_size)
        return _render_tool(request, slug, template, {'success': True, 'job': job, 'result': result})
    except (ProcessingError, file_utils.UnsafeFileError) as exc:
        _finish_job(job, result_path=None, error=exc)
        file_utils.cleanup_dir(output_dir)
        return _render_tool(request, slug, template, {'form': form, 'error': str(exc)}, status=400)
    except Exception:
        logger.exception('Unexpected error in %s', slug)
        _finish_job(job, result_path=None, error='Unexpected error')
        file_utils.cleanup_dir(output_dir)
        return _render_tool(request, slug, template,
                             {'form': form, 'error': 'Something went wrong while building your PDF.'}, status=500)
    finally:
        file_utils.cleanup_dir(upload_dir)


@require_http_methods(['GET', 'POST'])
def jpg_to_pdf(request):
    return _image_to_pdf_view(request, 'jpg-to-pdf', 'pdf_tools/tool_image_to_pdf.html')


@require_http_methods(['GET', 'POST'])
def png_to_pdf(request):
    return _image_to_pdf_view(request, 'png-to-pdf', 'pdf_tools/tool_image_to_pdf.html')
