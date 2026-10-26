from django.core.exceptions import ValidationError
from PIL import Image

MAX_IMAGE_BYTES = 2 * 1024 * 1024
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}


def validate_image_upload(f):
    """Reject oversize files and anything Pillow cannot decode as JPEG/PNG/WebP
    (extension and content-type headers are attacker-controlled; the pixel
    data is what we verify)."""
    if f.size > MAX_IMAGE_BYTES:
        raise ValidationError("Image must be 2 MB or smaller.")
    try:
        pos = f.tell()
        with Image.open(f) as img:
            img.verify()
            fmt = img.format
        f.seek(pos)
    except Exception as exc:  # noqa: BLE001 - any decode failure is a rejection
        raise ValidationError("Upload a valid image file.") from exc
    if fmt not in ALLOWED_FORMATS:
        raise ValidationError("Only JPEG, PNG or WebP images are allowed.")
