from django.db.models.signals import m2m_changed
from django.db import router, transaction
from django.dispatch import receiver
from .models import VersionedModel


def _m2m_field_on(model, through):
    """Return the ManyToManyField declared on `model` that uses `through`, or None."""
    for field in model._meta.many_to_many:
        if field.remote_field.through is through:
            return field
    return None


@receiver(m2m_changed)
def track_m2m_changes(sender, instance, action, reverse, model, pk_set, **kwargs):
    """
    Automatically create versions when M2M relations change.

    Versions the object whose M2M field changed (`instance` for forward changes),
    and the objects on the other side whose own M2M field changed too: the
    forward objects for reverse changes (tag.post_set.add(post)) and the peers
    of symmetrical relations (person.friends.add(other)).
    """
    # The M2M field on the other side, if that side declares one
    other_field = _m2m_field_on(model, sender) if issubclass(model, VersionedModel) else None

    if action == "pre_clear":
        if other_field is not None:
            # pk_set is None for clears; remember who is affected before the rows are gone
            affected = model._base_manager.filter(**{other_field.name: instance}).values_list("pk", flat=True)
            instance.__dict__.setdefault("_m2m_cleared", {})[sender] = list(affected)
        return

    if action not in ("post_add", "post_remove", "post_clear"):
        return

    # Set by VersionedModel.rollback() while it restores relations; rollback records its own version
    if not reverse and isinstance(instance, VersionedModel) and not getattr(instance, "_skip_m2m_versioning", False):
        instance.save_version()

    if other_field is not None:
        if action == "post_clear":
            pks = instance.__dict__.get("_m2m_cleared", {}).pop(sender, [])
        else:
            pks = list(pk_set or ())

        def version_other_side():
            for obj in model._base_manager.filter(pk__in=pks):
                obj.save_version()

        if action == "post_add" and other_field.remote_field.symmetrical:
            # Django sends post_add before inserting the mirror rows of a symmetrical
            # relation, so the peers only see the change once the transaction commits
            transaction.on_commit(version_other_side, using=router.db_for_write(sender, instance=instance))
        else:
            version_other_side()
