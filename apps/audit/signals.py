from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from .services import log


@receiver(user_logged_in)
def _logged_in(sender, request, user, **kwargs):
    log("auth.login", user, user=user)


@receiver(user_logged_out)
def _logged_out(sender, request, user, **kwargs):
    if user is not None:
        log("auth.logout", user, user=user)


@receiver(user_login_failed)
def _login_failed(sender, credentials, request=None, **kwargs):
    # only the attempted username is stored - never the password
    log("auth.login_failed", None, username=str(credentials.get("username", ""))[:150])
