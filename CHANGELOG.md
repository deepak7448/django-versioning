# Changelog

## 0.2.0

### Upgrade notes

- Run `python manage.py migrate`: `VersionControl.object_id` changes from an integer to a text column so UUID and string primary keys can be versioned.
- Snapshots are now normalised to JSON (with full microsecond precision) before hashing. The first save of an existing object with date, time, decimal or UUID fields records one extra version.
- `QuerySet.delete()` on a `VersionedModel` queryset (including the admin's "delete selected" action) now soft-deletes. Use the new `QuerySet.hard_delete()` to delete permanently.
- Packaging moved from `setup.cfg`/`setup.py` to `pyproject.toml`.

### Fixed

- `rollback()` crashed on models with a foreign key.
- `rollback()` to a delete version un-deleted the record instead of soft-deleting it.
- `rollback()` set fields missing from an older snapshot to `None`, and didn't restore many-to-many relations.
- `rollback()` lost microseconds on datetime and time fields.
- The diff of each version was computed but saved as `null`.
- Saving a model with a symmetrical self many-to-many (or any cycle of relations) recursed forever.
- Bulk deletes permanently deleted rows without recording anything.
- Unique validation in forms ignored soft-deleted rows, so saving failed with an `IntegrityError`.
- Versions couldn't be recorded for models with UUID or string primary keys.
- Saves and their versions weren't written in one transaction; concurrent saves could claim the same version number.
- The admin crashed with custom user models, models not registered in the admin, and content types whose model was removed.
- `audit_trail()` left out the creation and failed on user models without a `username` field.
- `delete()` didn't record the request user as `deleted_by`, and deleting an already-deleted record recorded another version.
- Many-to-many changes made from the reverse side, or on the other side of a symmetrical relation, weren't versioned.
- The user foreign keys used `get_user_model()` at import time instead of `settings.AUTH_USER_MODEL`.

### Added

- `VersionedQuerySet.delete(user=None)` (soft) and `hard_delete(user=None)`.
- `version_control.managers.include_deleted()` context manager.
- `VersionControl.content_object` generic relation.
- An `action` key in `audit_trail()` entries.
- An example project in `examples/` and a test suite.

### Performance

- Saving no longer loads related objects that aren't already in memory; the query count no longer grows with the number of related rows.
- About three fewer queries per save, delete, restore and rollback: no savepoint, and no extra row lock when the row was just written.
- The version history admin loads the versioned objects with one query per content type instead of one per row.
- `diff_versions()` uses one query instead of two.
- The internal `current_version` update no longer sends an extra `post_save` signal.

## 0.1.x

See the Git history for earlier releases.
