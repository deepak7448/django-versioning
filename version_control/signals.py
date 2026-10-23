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

    The rows to be versioned are locked in the pre_* signal, before Django
    touches the through table. Inserting a through row takes a shared lock on
    the referenced row for the foreign key check (InnoDB), so locking only
    afterwards would let concurrent writers deadlock upgrading that lock.
    """
    # The M2M field on the other side, if that side declares one
    other_field = _m2m_field_on(model, sender) if issubclass(model, VersionedModel) else None
    version_instance = not reverse and isinstance(instance, VersionedModel)
    # Django sends post_add before inserting the mirror rows of a symmetrical relation,
    # so peers are versioned once the transaction commits (see below)
    defer_other_side = other_field is not None and other_field.remote_field.symmetrical and action in ("pre_add", "post_add")
    using = router.db_for_write(sender, instance=instance)
    state = instance.__dict__.setdefault("_m2m_versioning", {})

    if action in ("pre_add", "pre_remove", "pre_clear"):
        other_pks = []
        if other_field is not None and not defer_other_side:
            if action == "pre_clear":
                # pk_set is None for clears; find who is affected before the rows are gone
                other_pks = list(model._base_manager.filter(**{other_field.name: instance}).values_list("pk", flat=True))
            else:
                other_pks = list(pk_set or ())
        state[sender] = other_pks

        if version_instance and other_field is not None and model is instance.__class__:
            # Symmetrical or self-referential: one ordered lock covers both sides
            model._lock_rows([instance.pk, *other_pks], using=using)
        else:
            if version_instance:
                instance._lock_rows([instance.pk], using=using)
            if other_pks:
                model._lock_rows(other_pks, using=using)
        return

    if action not in ("post_add", "post_remove", "post_clear"):
        return

    other_pks = state.pop(sender, [])

    # Set by VersionedModel.rollback() while it restores relations; rollback records its own version
    if version_instance and not getattr(instance, "_skip_m2m_versioning", False):
        instance.save_version(_locked=True)

    if other_field is None:
        return

    if defer_other_side:
        pks = list(pk_set or ())

        def version_peers():
            for obj in model._base_manager.filter(pk__in=pks):
                obj.save_version()

        transaction.on_commit(version_peers, using=using)
    else:
        for obj in model._base_manager.filter(pk__in=other_pks):
            obj.save_version(_locked=True)
