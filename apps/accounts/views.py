from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from apps.audit.services import log as audit_log
from apps.core.mixins import AppPermissionMixin

from .forms import LoginForm, UserCreateForm, UserUpdateForm
from .models import User


class LoginView(auth_views.LoginView):
    template_name = "accounts/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True


class UserListView(AppPermissionMixin, ListView):
    permission_required = "accounts.view_user"
    model = User
    template_name = "accounts/user_list.html"
    paginate_by = 25

    def get_queryset(self):
        return User.objects.prefetch_related("groups").order_by("username")


class UserCreateView(AppPermissionMixin, CreateView):
    permission_required = "accounts.add_user"
    form_class = UserCreateForm
    template_name = "accounts/user_form.html"
    success_url = reverse_lazy("accounts:user_list")

    @transaction.atomic
    def form_valid(self, form):
        response = super().form_valid(form)
        audit_log("user.create", self.object, roles=[g.name for g in form.cleaned_data["roles"]])
        messages.success(self.request, f"User {self.object.username} created.")
        return response


class UserUpdateView(AppPermissionMixin, UpdateView):
    permission_required = "accounts.change_user"
    model = User
    form_class = UserUpdateForm
    template_name = "accounts/user_form.html"
    success_url = reverse_lazy("accounts:user_list")

    def form_valid(self, form):
        target = self.object
        if target.pk == self.request.user.pk:
            # prevents self-lockout and self-escalation
            if (
                set(form.cleaned_data["roles"]) != set(target.groups.all())
                or not form.cleaned_data["is_active"]
            ):
                messages.error(self.request, "You cannot change your own roles or deactivate yourself.")
                return self.form_invalid(form)
        with transaction.atomic():
            before_roles = sorted(target.groups.values_list("name", flat=True))
            before_active = target.is_active
            response = super().form_valid(form)
            self.object.groups.set(form.cleaned_data["roles"])
            after_roles = sorted(g.name for g in form.cleaned_data["roles"])
            if before_roles != after_roles:
                audit_log("user.roles_changed", self.object, before=before_roles, after=after_roles)
            if before_active != self.object.is_active:
                audit_log("user.activated" if self.object.is_active else "user.deactivated", self.object)
        messages.success(self.request, "User updated.")
        return response


class UserToggleActiveView(AppPermissionMixin, View):
    permission_required = "accounts.change_user"

    def post(self, request, pk):
        user = get_object_or_404(User, pk=pk)
        if user.pk == request.user.pk:
            messages.error(request, "You cannot deactivate your own account.")
        else:
            user.is_active = not user.is_active
            user.save(update_fields=["is_active"])
            audit_log("user.activated" if user.is_active else "user.deactivated", user)
            messages.success(request, f"{user.username} is now {'active' if user.is_active else 'inactive'}.")
        return redirect("accounts:user_list")


class PasswordChangeView(LoginRequiredMixin, auth_views.PasswordChangeView):
    template_name = "accounts/password_change.html"
    success_url = reverse_lazy("accounts:password_change_done")

    def form_valid(self, form):
        audit_log("user.password_changed", self.request.user)
        return super().form_valid(form)
