from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _

class VersioningConfig(AppConfig):
    name = "version_control"
    verbose_name = _("Versioning")
    default_auto_field = "django.db.models.BigAutoField"



    def ready(self):
        from . import signals 