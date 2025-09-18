from django.db.models.signals import m2m_changed
from django.dispatch import receiver
from .models import VersionedModel


@receiver(m2m_changed)
def track_m2m_changes(sender, instance, action, reverse, model, pk_set, **kwargs):
    """
    Automatically create version when M2M relations change.
    """
    if not isinstance(instance, VersionedModel):
        return

    if action in ["post_add", "post_remove", "post_clear"]:
        instance.save_version(user=getattr(instance, "_version_user", None))
