from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('about/', views.about, name='about'),
    path('privacy-policy/', views.privacy, name='privacy'),
    path('terms-of-service/', views.terms, name='terms'),
    path('faq/', views.faq, name='faq'),

    # Auth
    path('signup/', views.signup, name='signup'),
    path('login/', auth_views.LoginView.as_view(template_name='registration/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('dashboard/', views.dashboard, name='dashboard'),

    # Downloads
    path('download/<uuid:job_id>/', views.download, name='download'),

    # Conversion
    path('pdf-to-word/', views.pdf_to_word, name='pdf-to-word'),
    path('pdf-to-jpg/', views.pdf_to_jpg, name='pdf-to-jpg'),
    path('pdf-to-png/', views.pdf_to_png, name='pdf-to-png'),
    path('jpg-to-pdf/', views.jpg_to_pdf, name='jpg-to-pdf'),
    path('png-to-pdf/', views.png_to_pdf, name='png-to-pdf'),
    path('pdf-to-text/', views.pdf_to_text, name='pdf-to-text'),
    path('text-to-pdf/', views.text_to_pdf, name='text-to-pdf'),

    # Organization
    path('merge-pdf/', views.merge_pdf, name='merge-pdf'),
    path('split-pdf/', views.split_pdf, name='split-pdf'),
    path('delete-pages/', views.delete_pages, name='delete-pages'),
    path('extract-pages/', views.extract_pages, name='extract-pages'),
    path('reorder-pages/', views.reorder_pages, name='reorder-pages'),
    path('rotate-pdf/', views.rotate_pdf, name='rotate-pdf'),

    # Optimization
    path('compress-pdf/', views.compress_pdf, name='compress-pdf'),

    # Security
    path('protect-pdf/', views.protect_pdf, name='protect-pdf'),
    path('unlock-pdf/', views.unlock_pdf, name='unlock-pdf'),

    # Editing
    path('watermark-pdf/', views.watermark_pdf, name='watermark-pdf'),
    path('page-numbers/', views.page_numbers, name='page-numbers'),
    path('edit-metadata/', views.edit_metadata, name='edit-metadata'),
    path('pdf-info/', views.pdf_info, name='pdf-info'),
]
