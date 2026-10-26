"""Django settings for the Retail Application.

Every environment-specific value comes from environment variables (optionally
loaded from a ``.env`` file that is never committed). See ``.env.example``.
"""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, []),
    CSRF_TRUSTED_ORIGINS=(list, []),
    SECURE_SSL_REDIRECT=(bool, False),
    SESSION_COOKIE_AGE=(int, 8 * 60 * 60),
    MAX_UPLOAD_MB=(int, 5),
    TAX_DEFAULT_RATE=(str, "0.00"),
)
environ.Env.read_env(BASE_DIR / ".env")  # silently ignored when the file is absent

DEBUG = env("DEBUG")
# No insecure fallback: a missing SECRET_KEY must fail loudly outside DEBUG.
SECRET_KEY = env("SECRET_KEY", default="dev-only-insecure-key-do-not-use" if DEBUG else None)
if not SECRET_KEY:
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured(
        "SECRET_KEY must be set (see .env.example); there is no insecure fallback outside DEBUG."
    )
ALLOWED_HOSTS = env("ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env("CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    # third party
    "rest_framework",
    "rest_framework.authtoken",
    "django_filters",
    "drf_spectacular",
    "drf_spectacular_sidecar",
    "axes",
    # project apps
    "apps.core",
    "apps.accounts",
    "apps.audit",
    "apps.products",
    "apps.suppliers",
    "apps.customers",
    "apps.inventory",
    "apps.purchasing",
    "apps.sales",
    "apps.payments",
    "apps.returns",
    "apps.reports",
    "apps.api",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "axes.middleware.AxesMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.SecurityHeadersMiddleware",
    "apps.audit.middleware.AuditContextMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.business",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# --- Database -------------------------------------------------------------
# PostgreSQL is the only supported database. DATABASE_URL is mandatory.
DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=60)
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True
DATABASES["default"].setdefault("OPTIONS", {})
# Optional read-only connection to the legacy Flask database, used exclusively
# by ``manage.py import_legacy``.
if env("LEGACY_DATABASE_URL", default=""):
    DATABASES["legacy"] = env.db("LEGACY_DATABASE_URL")
    # defence in depth: the importer can never write to the legacy database
    DATABASES["legacy"].setdefault("OPTIONS", {})["options"] = "-c default_transaction_read_only=on"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Auth -----------------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "core:dashboard"
LOGOUT_REDIRECT_URL = "accounts:login"
PASSWORD_RESET_TIMEOUT = 60 * 60  # reset links live for one hour

# Brute-force protection (django-axes)
AXES_FAILURE_LIMIT = env.int("AXES_FAILURE_LIMIT", default=5)
AXES_COOLOFF_TIME = 0.25  # hours (15 minutes)
AXES_LOCKOUT_PARAMETERS = ["username", "ip_address"]
AXES_RESET_ON_SUCCESS = True

# --- Sessions / cookies ---------------------------------------------------
SESSION_COOKIE_AGE = env("SESSION_COOKIE_AGE")
SESSION_SAVE_EVERY_REQUEST = True  # sliding expiry: idle sessions expire
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

# --- Internationalisation --------------------------------------------------
# Legacy timestamps are naive; they are interpreted in this zone on import.
LANGUAGE_CODE = "en-us"
TIME_ZONE = env("TIME_ZONE", default="Asia/Kuala_Lumpur")
USE_I18N = True
USE_TZ = True

# --- Static & media -------------------------------------------------------
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
        if not DEBUG
        else "django.contrib.staticfiles.storage.StaticFilesStorage"
    },
}
MEDIA_URL = "media/"
MEDIA_ROOT = Path(env("MEDIA_ROOT", default=str(BASE_DIR / "media")))

MAX_UPLOAD_MB = env("MAX_UPLOAD_MB")
DATA_UPLOAD_MAX_MEMORY_SIZE = MAX_UPLOAD_MB * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FIELDS = 5000  # large invoices post many fields
FILE_UPLOAD_PERMISSIONS = 0o640

# --- Security headers / HTTPS ----------------------------------------------
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
if not DEBUG:
    SECURE_SSL_REDIRECT = env("SECURE_SSL_REDIRECT")
    SESSION_COOKIE_SECURE = env.bool("SESSION_COOKIE_SECURE", default=True)
    CSRF_COOKIE_SECURE = env.bool("CSRF_COOKIE_SECURE", default=True)
    SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=31536000)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = False  # opt in deliberately once HTTPS is permanent
    # W021 only reminds us that HSTS *preload* is off. Preloading is effectively irreversible, so it
    # stays a conscious opt-in rather than a default.
    SILENCED_SYSTEM_CHECKS = ["security.W021"]
    if env.bool("TRUST_PROXY_SSL_HEADER", default=False):
        SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# --- Email (password reset) -------------------------------------------------
EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", default="localhost")
EMAIL_PORT = env.int("EMAIL_PORT", default=25)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=False)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="no-reply@localhost")

# --- REST framework ---------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.TokenAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.DjangoModelPermissions"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.UserRateThrottle",
        "rest_framework.throttling.AnonRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {"user": "600/min", "anon": "30/min"},
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "apps.core.exceptions.api_exception_handler",
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
}
SPECTACULAR_SETTINGS = {
    "TITLE": "Retail Application API",
    "DESCRIPTION": "Products, customers, invoices and stock for the retail management system.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SWAGGER_UI_DIST": "SIDECAR",
    "SWAGGER_UI_FAVICON_HREF": "SIDECAR",
    "REDOC_DIST": "SIDECAR",
}

# --- Business configuration -------------------------------------------------
BUSINESS = {
    "NAME": env("COMPANY_NAME", default="ASKER ENTERPRISE"),
    "ADDRESS": env(
        "COMPANY_ADDRESS", default="NO: 2404 4TH FLOOR MALAYAN MANSION, MASJID INDIA, KUALA LUMPUR - 50100"
    ),
    "CONTACT": env("COMPANY_CONTACT", default="0182843318"),
    "EMAIL": env("COMPANY_EMAIL", default="ajmeerdon@gmail.com"),
    "TAX_ID": env("COMPANY_TAX_ID", default=""),
    "CURRENCY": env("CURRENCY_SYMBOL", default="RM"),
    "TAX_LABEL": env("TAX_LABEL", default="GST"),
    "INVOICE_NOTES": [
        "Interest of 1.5% will be imposed after 30 days of delivery of goods on credit period.",
        "All cheques should be crossed and made payable to ASKER ENTERPRISE.",
        "All cash payments must be made directly to the Account Department.",
        "The property of the goods in this bill shall remain with the seller until full payment "
        "has been received. The seller has the right of entry and seizure in case of non-payment.",
        "Please inform us of any discrepancies within 1 day from the invoice date.",
    ],
}
# Default tax rate applied to NEW products. The legacy database stored no tax at
# all (the printed invoice showed a hard-coded 10% that was never persisted), so
# the default is 0 and must be set per product from a verified tax rule.
TAX_DEFAULT_RATE = env("TAX_DEFAULT_RATE")
INVOICE_NUMBER_START = 100000  # matches the legacy invoice sequence

# --- Logging -----------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "structured": {
            "format": "ts=%(asctime)s level=%(levelname)s logger=%(name)s msg=%(message)r",
        },
    },
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "structured"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
    "loggers": {
        # never log request bodies (they can carry passwords)
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "retail": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}

HEALTH_CHECK_ENABLED = True
