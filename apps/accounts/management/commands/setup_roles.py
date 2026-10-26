from django.core.management.base import BaseCommand

from apps.accounts.roles import sync_roles


class Command(BaseCommand):
    help = "Create or refresh the role groups and their permissions (idempotent)."

    def handle(self, *args, **options):
        sync_roles()
        self.stdout.write(self.style.SUCCESS("Roles synchronised."))
