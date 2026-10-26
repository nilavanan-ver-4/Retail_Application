from django.db import models, transaction


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class DocumentSequence(models.Model):
    """Concurrency-safe, gap-tolerant counters for document numbers."""

    name = models.CharField(max_length=60, unique=True)
    last_value = models.BigIntegerField(default=0)

    class Meta:
        db_table = "core_document_sequence"

    def __str__(self):
        return f"{self.name}={self.last_value}"

    @classmethod
    def next_value(cls, name: str, start: int = 1) -> int:
        """Allocate the next number. Must run inside ``transaction.atomic``:
        the row lock serialises concurrent allocators, so two invoices can never
        receive the same number."""
        with transaction.atomic():
            seq, _ = cls.objects.select_for_update().get_or_create(
                name=name, defaults={"last_value": start - 1})
            seq.last_value += 1
            seq.save(update_fields=["last_value"])
            return seq.last_value

    @classmethod
    def ensure_at_least(cls, name: str, value: int) -> None:
        with transaction.atomic():
            seq, _ = cls.objects.select_for_update().get_or_create(name=name)
            if seq.last_value < value:
                seq.last_value = value
                seq.save(update_fields=["last_value"])


class ImportJob(models.Model):
    """A validated-but-not-yet-applied spreadsheet import.

    Only the parsed rows are stored - the uploaded file itself is never written
    to disk - so there is no uploaded-file attack surface after parsing.
    """

    class Status(models.TextChoices):
        PREVIEW = "preview", "Awaiting confirmation"
        APPLIED = "applied", "Applied"
        CANCELLED = "cancelled", "Cancelled"

    kind = models.CharField(max_length=30)
    uploaded_by = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL,
                                    related_name="+")
    original_name = models.CharField(max_length=255)
    mode = models.CharField(max_length=20, default="create")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PREVIEW)
    rows = models.JSONField(default=list)  # [{"line": 2, "data": {...}, "action": "create", "errors": []}]
    summary = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "core_import_job"
        ordering = ["-created_at"]
