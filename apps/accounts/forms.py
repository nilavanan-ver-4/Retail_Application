from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.models import Group

from .models import User


class LoginForm(AuthenticationForm):
    username = forms.CharField(widget=forms.TextInput(attrs={"autofocus": True,
                                                            "autocomplete": "username"}))


class RoleMixin(forms.Form):
    roles = forms.ModelMultipleChoiceField(
        queryset=Group.objects.order_by("name"), required=False,
        widget=forms.CheckboxSelectMultiple, help_text="Roles control what this user can do.")


class UserCreateForm(UserCreationForm, RoleMixin):
    class Meta:
        model = User
        fields = ("username", "first_name", "last_name", "email", "phone")

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            user.groups.set(self.cleaned_data["roles"])
        return user


class UserUpdateForm(forms.ModelForm, RoleMixin):
    class Meta:
        model = User
        fields = ("first_name", "last_name", "email", "phone", "is_active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["roles"].initial = self.instance.groups.all()
