class SecurityHeadersMiddleware:
    """Adds a strict Content-Security-Policy and other hardening headers.

    Scripts are only allowed from our own origin (no inline scripts), which is
    the main XSS mitigation layered on top of template auto-escaping.
    """

    CSP = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self' data:; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'"
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.headers.setdefault("Content-Security-Policy", self.CSP)
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if request.user.is_authenticated:
            # authenticated pages hold business data: keep them out of shared caches
            response.headers.setdefault("Cache-Control", "no-store")
        return response
