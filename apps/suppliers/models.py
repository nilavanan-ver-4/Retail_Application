from django.db import models

from apps.core.models import TimeStampedModel


class Supplier(TimeStampedModel):
    name = models.CharField(max_length=100, help_text="Contact person")
    company = models.CharField(max_length=255, blank=True)
    mobile = models.CharField(max_length=30, blank=True)
    email = models.EmailField(max_length=100, blank=True)
    address = models.TextField(blank=True)
    terms = models.TextField(blank=True, help_text="Payment / delivery terms")
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "suppliers_supplier"
        ordering = ["name"]
        indexes = [models.Index(fields=["name"]), models.Index(fields=["company"])]

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("suppliers:detail", args=[self.pk])

    def __str__(self):
        return f"{self.name} ({self.company})" if self.company else self.name
