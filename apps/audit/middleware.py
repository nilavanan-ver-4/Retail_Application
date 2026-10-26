from .services import reset_context, set_context


def client_ip(request):
    # Only REMOTE_ADDR is trusted; set it correctly behind a proxy (gunicorn
    # --forwarded-allow-ips) rather than trusting a spoofable header here.
    return request.META.get("REMOTE_ADDR")


class AuditContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = set_context(getattr(request, "user", None), client_ip(request))
        try:
            return self.get_response(request)
        finally:
            reset_context(token)
