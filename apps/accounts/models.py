from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Custom user from day one so the model can evolve without a painful swap."""

    phone = models.CharField(max_length=30, blank=True)

    class Meta:
        db_table = "accounts_user"

    @property
    def role_names(self):
        return list(self.groups.values_list("name", flat=True))

    def display_name(self):
        return self.get_full_name() or self.username
