from django.apps import AppConfig
from django.db.models.signals import post_migrate


def _sync_roles(sender, **kwargs):
    from .roles import sync_roles

    sync_roles()


class AccountsConfig(AppConfig):
    name = "apps.accounts"
    label = "accounts"

    def ready(self):
        # post_migrate fires once per app in INSTALLED_APPS order; the "api" app is
        # last, so every custom permission already exists when roles are synced.
        post_migrate.connect(_sync_roles, sender=self._last_app(), dispatch_uid="sync_roles")

    @staticmethod
    def _last_app():
        from django.apps import apps

        return apps.get_app_config("api")
