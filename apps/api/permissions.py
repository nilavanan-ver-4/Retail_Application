from rest_framework.permissions import DjangoModelPermissions


class ModelPermissionsWithView(DjangoModelPermissions):
    """DRF's default does not require *view* permission for reads; ours does,
    so a user without ``view_customer`` cannot list customers over the API."""

    perms_map = {
        **DjangoModelPermissions.perms_map,
        "GET": ["%(app_label)s.view_%(model_name)s"],
        "HEAD": ["%(app_label)s.view_%(model_name)s"],
        "OPTIONS": [],
    }
