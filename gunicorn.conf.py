"""Gunicorn settings. Override with environment variables; no secrets here."""

import multiprocessing
import os

bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8000")
workers = int(os.environ.get("GUNICORN_WORKERS", max(2, multiprocessing.cpu_count() * 2 + 1)))
threads = int(os.environ.get("GUNICORN_THREADS", 2))
worker_class = "gthread"
timeout = 60
graceful_timeout = 30
keepalive = 5
max_requests = 2000  # recycle workers to bound memory growth
max_requests_jitter = 200
accesslog = "-"
errorlog = "-"
# the access log must not contain query strings carrying search terms that could be personal data
access_log_format = '%(h)s "%(m)s %(U)s" %(s)s %(b)s %(L)s'
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1")
