"""
Django settings for the Pdfino project.

Split cleanly enough that moving to PostgreSQL / production only requires
changing environment variables, not code.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Load variables from a local .env file if python-dotenv is installed and the
# file exists. This is optional - the app also works fine with real
# environment variables set by your OS, Docker, or hosting platform.
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / '.env')
except ImportError:
    pass


def env(key, default=None, cast=str):
    val = os.environ.get(key, default)
    if val is None:
        return None
    if cast is bool:
        return str(val).lower() in ('1', 'true', 'yes', 'on')
    if cast is int:
        return int(val)
    return cast(val)


# ------------------------------------------------------------------
# Core / security
# ------------------------------------------------------------------
SECRET_KEY = env('SECRET_KEY', 'django-insecure-CHANGE-ME-IN-PRODUCTION')
DEBUG = env('DEBUG', 'True', cast=bool)

ALLOWED_HOSTS = [
    h.strip() for h in env('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',') if h.strip()
]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',

    'pdf_tools',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'pdf_tools.middleware.FriendlyErrorMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'pdf_tools' / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'pdf_tools.context_processors.site_meta',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'

# ------------------------------------------------------------------
# Database - SQLite for dev, DATABASE_URL-style override for prod
# ------------------------------------------------------------------
DATABASE_URL = env('DATABASE_URL', '')

if DATABASE_URL.startswith('postgres'):
    import re
    m = re.match(
        r'postgres(?:ql)?://(?P<user>[^:]+):(?P<password>[^@]*)@(?P<host>[^:/]+):?(?P<port>\d*)/(?P<name>.+)',
        DATABASE_URL,
    )
    if not m:
        raise ValueError('Invalid DATABASE_URL for PostgreSQL')
    g = m.groupdict()
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': g['name'],
            'USER': g['user'],
            'PASSWORD': g['password'],
            'HOST': g['host'],
            'PORT': g['port'] or '5432',
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 8}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# ------------------------------------------------------------------
# I18N
# ------------------------------------------------------------------
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# ------------------------------------------------------------------
# Static & media
# ------------------------------------------------------------------
STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'pdf_tools' / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Directories used for transient processing (auto-cleaned)
UPLOAD_TMP_DIR = MEDIA_ROOT / 'uploads'
OUTPUT_TMP_DIR = MEDIA_ROOT / 'outputs'
for _d in (UPLOAD_TMP_DIR, OUTPUT_TMP_DIR):
    _d.mkdir(parents=True, exist_ok=True)

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ------------------------------------------------------------------
# Upload / processing limits
# ------------------------------------------------------------------
MAX_UPLOAD_SIZE = env('MAX_UPLOAD_SIZE', str(50 * 1024 * 1024), cast=int)  # 50 MB default
MAX_UPLOAD_FILES = env('MAX_UPLOAD_FILES', '30', cast=int)
DATA_UPLOAD_MAX_MEMORY_SIZE = MAX_UPLOAD_SIZE
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024  # spill to disk above 5MB
FILE_UPLOAD_PERMISSIONS = 0o640

# How long generated output files are kept before the cleanup job deletes them
FILE_RETENTION_MINUTES = env('FILE_RETENTION_MINUTES', '60', cast=int)

# ------------------------------------------------------------------
# Auth redirects
# ------------------------------------------------------------------
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'dashboard'
LOGOUT_REDIRECT_URL = 'home'

# ------------------------------------------------------------------
# Security headers (relaxed defaults for local dev; tighten via env in prod)
# ------------------------------------------------------------------
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = False  # must be readable for JS-driven upload forms using the CSRF token
SECURE_BROWSER_XSS_FILTER = True

if not DEBUG:
    SECURE_SSL_REDIRECT = env('SECURE_SSL_REDIRECT', 'True', cast=bool)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True

# ------------------------------------------------------------------
# Logging - technical details go to a file, never to the user
# ------------------------------------------------------------------
LOG_DIR = BASE_DIR / 'logs'
LOG_DIR.mkdir(exist_ok=True)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {'format': '{asctime} {levelname} {name} {message}', 'style': '{'},
    },
    'handlers': {
        'console': {'class': 'logging.StreamHandler', 'formatter': 'verbose'},
        'file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': LOG_DIR / 'pdfino.log',
            'maxBytes': 5 * 1024 * 1024,
            'backupCount': 5,
            'formatter': 'verbose',
        },
    },
    'root': {'handlers': ['console', 'file'], 'level': 'INFO'},
    'loggers': {
        'pdf_tools': {'handlers': ['console', 'file'], 'level': 'DEBUG' if DEBUG else 'INFO', 'propagate': False},
        'django.request': {'handlers': ['console', 'file'], 'level': 'ERROR', 'propagate': False},
    },
}

MESSAGE_TAGS = {}
