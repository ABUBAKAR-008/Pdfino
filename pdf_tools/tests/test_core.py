"""
Automated tests for Pdfino's core logic.

These focus on the parts that are safe and fast to test without a browser:
file validation, page-range parsing, and the PDF service functions
themselves (built with PyMuPDF/reportlab test fixtures generated on the fly).
View-level tests use Django's test client to exercise the full request cycle.
"""
import io
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

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
