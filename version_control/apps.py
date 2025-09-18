from django.apps import AppConfig

class VersioningConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "version_control"
    verbose_name = "Django Versioning"



    def ready(self):
        from . import signals 