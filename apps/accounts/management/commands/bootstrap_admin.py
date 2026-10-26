"""Create the first administrator from environment variables (non-interactive).

    BOOTSTRAP_ADMIN_USERNAME, BOOTSTRAP_ADMIN_PASSWORD, BOOTSTRAP_ADMIN_EMAIL

Does nothing if the user already exists, so it is safe in deployment scripts.
The password is never printed or logged.
"""

import os

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create the initial Administrator account from environment variables."

    def handle(self, *args, **options):
        username = os.environ.get("BOOTSTRAP_ADMIN_USERNAME")
        password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD")
        if not username or not password:
            raise CommandError("Set BOOTSTRAP_ADMIN_USERNAME and BOOTSTRAP_ADMIN_PASSWORD.")
        User = get_user_model()
        if User.objects.filter(username=username).exists():
            self.stdout.write("Administrator already exists; nothing to do.")
            return
        user = User.objects.create_user(
            username=username,
            password=password,
            email=os.environ.get("BOOTSTRAP_ADMIN_EMAIL", ""),
            is_staff=True,
            is_superuser=True,
        )
        user.groups.add(Group.objects.get(name="Administrator"))
        self.stdout.write(self.style.SUCCESS(f"Administrator '{username}' created."))
