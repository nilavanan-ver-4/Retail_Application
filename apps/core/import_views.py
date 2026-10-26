"""Generic upload -> preview -> apply views shared by every importer."""

from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View

from apps.audit.services import log as audit_log

from .imports import summarise
from .mixins import AppPermissionMixin
from .models import ImportJob


class UploadForm(forms.Form):
    file = forms.FileField(label="CSV or Excel (.xlsx) file")
    mode = forms.ChoiceField(choices=(), required=False)

    def __init__(self, *args, modes=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["mode"].choices = modes
        self.fields["mode"].initial = modes[0][0] if modes else "create"


class ImportUploadView(AppPermissionMixin, View):
    importer_class = None
    title = "Import"
    back_url = "/"
    preview_url_name = ""
    template_name = "generic/import_upload.html"
    sample_url = ""

    def get(self, request):
        form = UploadForm(modes=self.importer_class.modes)
        return render(request, self.template_name, self.context(form))

    def context(self, form):
        imp = self.importer_class()
        return {
            "form": form,
            "title": self.title,
            "back_url": self.back_url,
            "required": imp.required,
            "aliases": imp.aliases,
        }

    def post(self, request):
        form = UploadForm(request.POST, request.FILES, modes=self.importer_class.modes)
        if not form.is_valid():
            return render(request, self.template_name, self.context(form))
        mode = form.cleaned_data.get("mode") or "create"
        try:
            rows = self.importer_class().parse(form.cleaned_data["file"], mode)
        except ValidationError as exc:
            form.add_error("file", exc)
            return render(request, self.template_name, self.context(form))
        job = ImportJob.objects.create(
            kind=self.importer_class.kind,
            uploaded_by=request.user,
            mode=mode,
            original_name=form.cleaned_data["file"].name[:255],
            rows=rows,
            summary=summarise(rows),
        )
        return redirect(self.preview_url_name, pk=job.pk)


class ImportPreviewView(AppPermissionMixin, View):
    importer_class = None
    title = "Import preview"
    done_url_name = ""
    template_name = "generic/import_preview.html"

    def get_job(self, request, pk):
        # A user can only act on their own pending import
        return get_object_or_404(ImportJob, pk=pk, kind=self.importer_class.kind, uploaded_by=request.user)

    def get(self, request, pk):
        job = self.get_job(request, pk)
        return render(
            request,
            self.template_name,
            {
                "job": job,
                "title": self.title,
                "rows": job.rows[:500],
                "truncated": len(job.rows) > 500,
                "done_url_name": self.done_url_name,
            },
        )

    def post(self, request, pk):
        job = self.get_job(request, pk)
        if job.status != ImportJob.Status.PREVIEW:
            messages.error(request, "This import was already processed.")
            return redirect(self.done_url_name)
        if request.POST.get("cancel"):
            job.status = ImportJob.Status.CANCELLED
            job.save(update_fields=["status"])
            messages.info(request, "Import cancelled; nothing was changed.")
            return redirect(self.done_url_name)
        skip_invalid = bool(request.POST.get("skip_invalid"))
        errors = [r for r in job.rows if r["errors"]]
        if errors and not skip_invalid:
            messages.error(
                request,
                "The file has invalid rows. Fix them, or tick "
                "'skip invalid rows' to import only the valid ones.",
            )
            return redirect(request.path)
        good = [r for r in job.rows if not r["errors"]]
        with transaction.atomic():
            # lock the job row so a double-click cannot apply twice
            job = type(job).objects.select_for_update().get(pk=job.pk)
            if job.status != ImportJob.Status.PREVIEW:
                messages.error(request, "This import was already processed.")
                return redirect(self.done_url_name)
            result = self.importer_class().apply(good, request.user)
            job.status = ImportJob.Status.APPLIED
            job.applied_at = timezone.now()
            job.summary = {**job.summary, "applied": len(good), "skipped": len(errors)}
            job.save(update_fields=["status", "applied_at", "summary"])
            audit_log(
                f"import.{job.kind}",
                job,
                summary=f"{len(good)} rows applied, {len(errors)} skipped from {job.original_name}",
                **(result or {}),
            )
        messages.success(request, f"Imported {len(good)} row(s); skipped {len(errors)}.")
        return redirect(self.done_url_name)
