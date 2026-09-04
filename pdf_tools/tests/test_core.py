"""
Automated tests for Pdfino's core logic.

These focus on the parts that are safe and fast to test without a browser:
file validation, page-range parsing, and the PDF service functions
themselves (built with PyMuPDF/reportlab test fixtures generated on the fly).
View-level tests use Django's test client to exercise the full request cycle.
"""
import io
import os
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core import signing
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from pdf_tools.models import ConversionJob, StagedDocument
from pdf_tools.services.exceptions import ProcessingError
from pdf_tools.services.processing_guard import ProcessingAdmission, run_with_timeout
from pdf_tools.utils import staging
from pdf_tools.utils import files as file_utils
from pdf_tools.utils.files import UnsafeFileError, validate_image_upload, validate_pdf_upload
from pdf_tools.utils.pages import PageRangeError, parse_page_ranges


def make_pdf_bytes(num_pages=3):
    import fitz
    doc = fitz.open()
    for i in range(num_pages):
        page = doc.new_page()
        page.insert_text((72, 72), f'Page {i + 1}')
    data = doc.tobytes()
    doc.close()
    return data


def make_image_bytes(fmt='JPEG', size=(100, 100)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', size, color='red').save(buf, format=fmt)
    return buf.getvalue()


class PageRangeParsingTests(TestCase):
    def test_simple_range(self):
        self.assertEqual(parse_page_ranges('1-3', 5), [0, 1, 2])

    def test_mixed_list(self):
        self.assertEqual(parse_page_ranges('1-3, 5', 5), [0, 1, 2, 4])

    def test_deduplicates_and_sorts(self):
        self.assertEqual(parse_page_ranges('3,1,2,2', 5), [0, 1, 2])

    def test_out_of_range_rejected(self):
        with self.assertRaises(PageRangeError):
            parse_page_ranges('1-10', 5)

    def test_empty_input_rejected(self):
        with self.assertRaises(PageRangeError):
            parse_page_ranges('', 5)

    def test_invalid_characters_rejected(self):
        with self.assertRaises(PageRangeError):
            parse_page_ranges('1;DROP TABLE', 5)

    def test_reversed_range_normalizes(self):
        self.assertEqual(parse_page_ranges('3-1', 5), [0, 1, 2])


class FileValidationTests(TestCase):
    def test_valid_pdf_passes(self):
        f = SimpleUploadedFile('doc.pdf', make_pdf_bytes(), content_type='application/pdf')
        self.assertTrue(validate_pdf_upload(f))

    def test_fake_pdf_extension_rejected(self):
        f = SimpleUploadedFile('doc.pdf', b'not a real pdf', content_type='application/pdf')
        with self.assertRaises(UnsafeFileError):
            validate_pdf_upload(f)

    def test_empty_file_rejected(self):
        f = SimpleUploadedFile('doc.pdf', b'', content_type='application/pdf')
        with self.assertRaises(UnsafeFileError):
            validate_pdf_upload(f)

    def test_oversized_file_rejected(self):
        f = SimpleUploadedFile('doc.pdf', make_pdf_bytes(), content_type='application/pdf')
        with self.assertRaises(UnsafeFileError):
            validate_pdf_upload(f, max_size=10)

    def test_valid_jpg_passes(self):
        f = SimpleUploadedFile('img.jpg', make_image_bytes('JPEG'), content_type='image/jpeg')
        self.assertTrue(validate_image_upload(f))

    def test_valid_png_passes(self):
        f = SimpleUploadedFile('img.png', make_image_bytes('PNG'), content_type='image/png')
        self.assertTrue(validate_image_upload(f))

    def test_non_image_rejected(self):
        f = SimpleUploadedFile('img.jpg', b'definitely not an image', content_type='image/jpeg')
        with self.assertRaises(UnsafeFileError):
            validate_image_upload(f)

    @override_settings(MAX_IMAGE_PIXELS=100)
    def test_large_dimension_image_rejected(self):
        f = SimpleUploadedFile('large.png', make_image_bytes('PNG', size=(20, 20)), content_type='image/png')
        with self.assertRaises(UnsafeFileError):
            validate_image_upload(f)


class ServiceLayerTests(TestCase):
    """Exercises the PDF services directly against real, generated PDFs."""

    def setUp(self):
        import tempfile
        self.tmpdir = Path(tempfile.mkdtemp())

    def _write_pdf(self, name='in.pdf', pages=3):
        path = self.tmpdir / name
        path.write_bytes(make_pdf_bytes(pages))
        return path

    def test_merge_pdfs(self):
        from pdf_tools.services.organization import merge_pdfs, get_page_count
        a = self._write_pdf('a.pdf', 2)
        b = self._write_pdf('b.pdf', 3)
        out = self.tmpdir / 'merged.pdf'
        merge_pdfs([a, b], out)
        self.assertEqual(get_page_count(out), 5)

    def test_split_by_ranges(self):
        from pdf_tools.services.organization import split_by_ranges, get_page_count
        src = self._write_pdf('src.pdf', 6)
        out = split_by_ranges(src, [[0, 1], [2, 3, 4, 5]], self.tmpdir)
        self.assertEqual(len(out), 2)
        self.assertEqual(get_page_count(out[0]), 2)
        self.assertEqual(get_page_count(out[1]), 4)

    def test_rotate_pdf(self):
        from pdf_tools.services.organization import rotate_pdf
        import fitz
        src = self._write_pdf('rot.pdf', 1)
        out = self.tmpdir / 'rotated.pdf'
        rotate_pdf(src, out, 90, None)
        doc = fitz.open(out)
        self.assertEqual(doc.load_page(0).rotation, 90)
        doc.close()

    def test_delete_pages(self):
        from pdf_tools.services.organization import delete_pages, get_page_count
        src = self._write_pdf('del.pdf', 5)
        out = self.tmpdir / 'deleted.pdf'
        delete_pages(src, out, [1, 3])
        self.assertEqual(get_page_count(out), 3)

    def test_delete_all_pages_rejected(self):
        from pdf_tools.services.organization import delete_pages
        from pdf_tools.services.exceptions import ProcessingError
        src = self._write_pdf('del2.pdf', 2)
        out = self.tmpdir / 'deleted2.pdf'
        with self.assertRaises(ProcessingError):
            delete_pages(src, out, [0, 1])

    def test_extract_pages(self):
        from pdf_tools.services.organization import extract_pages, get_page_count
        src = self._write_pdf('ext.pdf', 5)
        out = self.tmpdir / 'extracted.pdf'
        extract_pages(src, out, [0, 2, 4])
        self.assertEqual(get_page_count(out), 3)

    def test_reorder_pages(self):
        from pdf_tools.services.organization import reorder_pages
        import fitz
        src = self._write_pdf('reorder.pdf', 3)
        out = self.tmpdir / 'reordered.pdf'
        reorder_pages(src, out, [2, 0, 1])
        doc = fitz.open(out)
        self.assertEqual(doc.page_count, 3)
        doc.close()

    def test_reorder_incomplete_order_rejected(self):
        from pdf_tools.services.organization import reorder_pages
        from pdf_tools.services.exceptions import ProcessingError
        src = self._write_pdf('reorder2.pdf', 3)
        out = self.tmpdir / 'reordered2.pdf'
        with self.assertRaises(ProcessingError):
            reorder_pages(src, out, [0, 1])  # missing page 2

    def test_compress_pdf(self):
        from pdf_tools.services.optimization import compress_pdf
        src = self._write_pdf('compress.pdf', 4)
        out = self.tmpdir / 'compressed.pdf'
        result = compress_pdf(src, out, 'medium')
        self.assertIn('compressed_size', result)
        self.assertTrue(out.exists())

    def test_protect_and_unlock_pdf(self):
        from pdf_tools.services.security import protect_pdf, unlock_pdf
        import fitz
        src = self._write_pdf('protect.pdf', 1)
        protected = self.tmpdir / 'protected.pdf'
        protect_pdf(src, protected, 'secret123')
        doc = fitz.open(protected)
        self.assertTrue(doc.is_encrypted)
        doc.close()

        unlocked = self.tmpdir / 'unlocked.pdf'
        unlock_pdf(protected, unlocked, 'secret123')
        doc2 = fitz.open(unlocked)
        self.assertFalse(doc2.is_encrypted)
        doc2.close()

    def test_unlock_wrong_password_rejected(self):
        from pdf_tools.services.security import protect_pdf, unlock_pdf
        from pdf_tools.services.exceptions import ProcessingError
        src = self._write_pdf('protect2.pdf', 1)
        protected = self.tmpdir / 'protected2.pdf'
        protect_pdf(src, protected, 'correct-password')
        with self.assertRaises(ProcessingError):
            unlock_pdf(protected, self.tmpdir / 'x.pdf', 'wrong-password')

    def test_metadata_roundtrip(self):
        from pdf_tools.services.security import get_metadata, set_metadata
        src = self._write_pdf('meta.pdf', 1)
        out = self.tmpdir / 'meta_out.pdf'
        set_metadata(src, out, {'title': 'My Title', 'author': 'Pdfino Tester'})
        meta = get_metadata(out)
        self.assertEqual(meta['title'], 'My Title')
        self.assertEqual(meta['author'], 'Pdfino Tester')

    def test_watermark_pdf(self):
        from pdf_tools.services.editing import watermark_pdf
        src = self._write_pdf('wm.pdf', 2)
        out = self.tmpdir / 'wm_out.pdf'
        result = watermark_pdf(src, out, 'CONFIDENTIAL')
        self.assertEqual(result['pages_watermarked'], 2)
        self.assertTrue(out.exists())

    def test_page_numbers(self):
        from pdf_tools.services.editing import add_page_numbers
        src = self._write_pdf('pn.pdf', 3)
        out = self.tmpdir / 'pn_out.pdf'
        result = add_page_numbers(src, out)
        self.assertEqual(result['page_count'], 3)

    def test_pdf_to_text(self):
        from pdf_tools.services.conversion import pdf_to_text
        src = self._write_pdf('text.pdf', 2)
        out = self.tmpdir / 'out.txt'
        result = pdf_to_text(src, out)
        self.assertEqual(result['page_count'], 2)
        self.assertTrue(out.exists())

    def test_text_to_pdf(self):
        from pdf_tools.services.conversion import text_to_pdf
        out = self.tmpdir / 'from_text.pdf'
        text_to_pdf('Hello Pdfino\n\nSecond paragraph.', out, 'Test Doc')
        self.assertTrue(out.exists())

    def test_text_to_pdf_empty_rejected(self):
        from pdf_tools.services.conversion import text_to_pdf
        from pdf_tools.services.exceptions import ProcessingError
        with self.assertRaises(ProcessingError):
            text_to_pdf('   ', self.tmpdir / 'empty.pdf', 'Empty')

    def test_pdf_to_images(self):
        from pdf_tools.services.conversion import pdf_to_images
        src = self._write_pdf('imgs.pdf', 2)
        out_dir = self.tmpdir / 'images_out'
        out_dir.mkdir()
        images = pdf_to_images(src, out_dir, image_format='jpg')
        self.assertEqual(len(images), 2)

    def test_images_to_pdf(self):
        from pdf_tools.services.conversion import images_to_pdf
        img1 = self.tmpdir / 'a.jpg'
        img1.write_bytes(make_image_bytes('JPEG'))
        img2 = self.tmpdir / 'b.jpg'
        img2.write_bytes(make_image_bytes('JPEG'))
        out = self.tmpdir / 'from_images.pdf'
        result = images_to_pdf([img1, img2], out)
        self.assertEqual(result['page_count'], 2)

    def test_pdf_info(self):
        from pdf_tools.services.editing import get_pdf_info
        src = self._write_pdf('info.pdf', 4)
        info = get_pdf_info(src)
        self.assertEqual(info['page_count'], 4)
        self.assertFalse(info['is_encrypted'])


class ViewSmokeTests(TestCase):
    """Basic end-to-end checks that every tool page loads and the homepage renders."""

    def test_homepage_loads(self):
        resp = self.client.get(reverse('home'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Pdfino')

    def test_all_tool_pages_load(self):
        from pdf_tools.tool_registry import TOOLS
        for tool in TOOLS:
            resp = self.client.get(reverse(tool['slug']))
            self.assertEqual(resp.status_code, 200, f"{tool['slug']} did not return 200")

    def test_static_pages_load(self):
        for name in ('about', 'privacy', 'terms', 'faq', 'signup', 'login'):
            resp = self.client.get(reverse(name))
            self.assertEqual(resp.status_code, 200)

    def test_merge_requires_two_files(self):
        f = SimpleUploadedFile('a.pdf', make_pdf_bytes(), content_type='application/pdf')
        resp = self.client.post(reverse('merge-pdf'), {'files': [f]})
        self.assertEqual(resp.status_code, 400)

    def test_merge_pdf_end_to_end(self):
        f1 = SimpleUploadedFile('a.pdf', make_pdf_bytes(2), content_type='application/pdf')
        f2 = SimpleUploadedFile('b.pdf', make_pdf_bytes(3), content_type='application/pdf')
        resp = self.client.post(reverse('merge-pdf'), {'files': [f1, f2]})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'merged')

    def test_invalid_file_shows_friendly_error(self):
        f = SimpleUploadedFile('fake.pdf', b'not a pdf', content_type='application/pdf')
        resp = self.client.post(reverse('compress-pdf'), {'file': f, 'level': 'medium'})
        self.assertEqual(resp.status_code, 400)
        self.assertContains(resp, 'valid PDF', status_code=400)

    def test_dashboard_requires_login(self):
        resp = self.client.get(reverse('dashboard'))
        self.assertEqual(resp.status_code, 302)

    def test_signup_and_dashboard_flow(self):
        resp = self.client.post(reverse('signup'), {
            'username': 'tester1', 'password1': 'a-strong-pw-93!', 'password2': 'a-strong-pw-93!',
        })
        self.assertEqual(resp.status_code, 302)
        resp2 = self.client.get(reverse('dashboard'))
        self.assertEqual(resp2.status_code, 200)


class DownloadAuthorizationTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.user_a = self.User.objects.create_user('download-a', password='strong-password-1')
        self.user_b = self.User.objects.create_user('download-b', password='strong-password-1')

    def make_job(self, user=None, status=ConversionJob.Status.SUCCESS, expires=None, with_file=True):
        job = ConversionJob.objects.create(
            user=user, tool_slug='text-to-pdf', tool_name='Text to PDF', status=status,
            expires_at=expires or timezone.now() + timedelta(minutes=10),
            result_filename='result.txt',
        )
        if with_file:
            output_dir = Path(settings.OUTPUT_TMP_DIR) / f'test-{job.id}'
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / 'result.txt').write_text('result', encoding='utf-8')
            job.result_relpath = str((output_dir / 'result.txt').relative_to(settings.OUTPUT_TMP_DIR))
            job.save(update_fields=['result_relpath'])
        return job

    def test_owner_can_download_own_result(self):
        job = self.make_job(self.user_a)
        self.client.force_login(self.user_a)
        self.assertEqual(self.client.get(reverse('download', args=[job.id])).status_code, 200)

    def test_user_cannot_download_another_users_result(self):
        job = self.make_job(self.user_a)
        self.client.force_login(self.user_b)
        self.assertEqual(self.client.get(reverse('download', args=[job.id])).status_code, 404)

    def test_anonymous_result_requires_valid_signed_token(self):
        job = self.make_job()
        token = signing.TimestampSigner(salt='pdfino-download').sign(str(job.id))
        self.assertEqual(self.client.get(reverse('download', args=[job.id]), {'token': token}).status_code, 200)
        self.assertEqual(self.client.get(reverse('download', args=[job.id]), {'token': 'tampered'}).status_code, 404)

    def test_expired_token_and_job_are_rejected(self):
        job = self.make_job(expires=timezone.now() - timedelta(seconds=1))
        token = signing.TimestampSigner(salt='pdfino-download').sign(str(job.id))
        self.assertEqual(self.client.get(reverse('download', args=[job.id]), {'token': token}).status_code, 404)

    def test_failed_and_missing_results_are_rejected(self):
        failed = self.make_job(status=ConversionJob.Status.FAILED, with_file=False)
        self.assertEqual(self.client.get(reverse('download', args=[failed.id])).status_code, 404)
        missing = self.make_job(with_file=False)
        self.assertEqual(self.client.get(reverse('download', args=[missing.id])).status_code, 404)


class StagingSecurityTests(TestCase):
    def request(self, user=None, session=None):
        request = RequestFactory().post('/reorder-pages/')
        session = session or SessionStore()
        if not session.session_key:
            session.save()
        request.session = session
        request.user = user or AnonymousUser()
        return request

    def test_staging_is_bound_to_anonymous_session_and_expires(self):
        upload = SimpleUploadedFile('stage.pdf', make_pdf_bytes(1), content_type='application/pdf')
        first_session = SessionStore()
        first = self.request(session=first_session)
        staged = staging.stage_upload(first, upload)
        self.assertIsNotNone(staging.get_staged(first, str(staged.token)))
        self.assertIsNone(staging.get_staged(first, 'not-a-staging-id'))
        self.assertIsNone(staging.get_staged(first, '00000000-0000-0000-0000-000000000001'))
        other = self.request()
        self.assertIsNone(staging.get_staged(other, str(staged.token)))
        staged.expires_at = timezone.now() - timedelta(seconds=1)
        staged.save(update_fields=['expires_at'])
        self.assertIsNone(staging.get_staged(first, str(staged.token)))

    def test_staging_is_bound_to_authenticated_user_and_cleanup_deletes_file(self):
        user_a = get_user_model().objects.create_user('stage-a', password='strong-password-1')
        user_b = get_user_model().objects.create_user('stage-b', password='strong-password-1')
        request_a = self.request(user_a)
        staged = staging.stage_upload(request_a, SimpleUploadedFile('stage.pdf', make_pdf_bytes(1)))
        self.assertIsNone(staging.get_staged(self.request(user_b), str(staged.token)))
        path = Path(settings.STAGING_TMP_DIR) / staged.relpath
        self.assertTrue(path.exists())
        staging.delete_staged(staged)
        self.assertFalse(path.exists())
        self.assertFalse(StagedDocument.objects.filter(token=staged.token).exists())


class SecurityRegressionTests(TestCase):
    def test_limits_are_configurable_and_filename_is_not_rendered_as_html(self):
        self.assertGreater(settings.MAX_UPLOAD_SIZE, 0)
        source = (Path(settings.BASE_DIR) / 'pdf_tools' / 'static' / 'js' / 'main.js').read_text(encoding='utf-8')
        self.assertNotIn("'<span class=\"pf-file-name\">' + file.name", source)
        self.assertIn('name.textContent = file.name', source)


class PhaseOneProductionTests(TestCase):
    def _settings_process(self, deployment_env, database_url, code):
        env = os.environ.copy()
        env.update({
            'DJANGO_SETTINGS_MODULE': 'config.settings',
            'DJANGO_ENV': deployment_env,
            'DEBUG': 'False' if deployment_env == 'production' else 'True',
            'SECRET_KEY': 'phase-one-test-secret-key',
            'DATABASE_URL': database_url,
        })
        return subprocess.run(
            [sys.executable, '-c', code],
            cwd=settings.BASE_DIR,
            env=env,
            capture_output=True,
            text=True,
        )

    def test_production_without_database_url_fails_closed(self):
        result = self._settings_process('production', '', 'import django; django.setup()')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('DATABASE_URL', result.stderr)

    def test_production_postgresql_configuration_loads(self):
        result = self._settings_process(
            'production',
            'postgresql://db-user:db-password@db.example.test:5433/pdfino?sslmode=require',
            "import django; django.setup(); from django.conf import settings; assert settings.DATABASES['default']['ENGINE'] == 'django.db.backends.postgresql'; assert settings.DATABASES['default']['PORT'] == '5433'",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_development_without_database_url_uses_sqlite(self):
        result = self._settings_process(
            'development', '',
            "import django; django.setup(); from django.conf import settings; assert settings.DATABASES['default']['ENGINE'] == 'django.db.backends.sqlite3'",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_processing_timeout_marks_job_failed_and_cleans_temp_files(self):
        job = ConversionJob.objects.create(
            tool_slug='compress-pdf', tool_name='Compress PDF', status=ConversionJob.Status.PROCESSING,
        )
        upload_dir = file_utils.new_upload_dir()
        output_dir = file_utils.new_output_dir()
        (upload_dir / 'input.pdf').write_bytes(b'input')
        (output_dir / 'partial.pdf').write_bytes(b'partial')
        try:
            run_with_timeout(time.sleep, 1, timeout=0.1)
        except ProcessingError as exc:
            from pdf_tools.views import _finish_job
            _finish_job(job, result_path=None, error=exc)
            file_utils.cleanup_dir(upload_dir)
            file_utils.cleanup_dir(output_dir)
        else:
            self.fail('Expected processing timeout')
        job.refresh_from_db()
        self.assertEqual(job.status, ConversionJob.Status.FAILED)
        self.assertFalse(upload_dir.exists())
        self.assertFalse(output_dir.exists())

    def test_concurrency_admission_limits_one_client(self):
        identity = 'phase-one-concurrency-test'
        self.assertTrue(ProcessingAdmission.acquire(identity))
        try:
            self.assertFalse(ProcessingAdmission.acquire(identity))
        finally:
            ProcessingAdmission.release(identity)
