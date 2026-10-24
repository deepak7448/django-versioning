# django-versioning

[![Django CI](https://github.com/deepak7448/django-versioning/actions/workflows/django.yml/badge.svg)](https://github.com/deepak7448/django-versioning/actions/workflows/django.yml)
[![PyPI](https://img.shields.io/pypi/v/django-versioning.svg)](https://pypi.org/project/django-versioning/)
[![Python versions](https://img.shields.io/pypi/pyversions/django-versioning.svg)](https://pypi.org/project/django-versioning/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/deepak7448/django-versioning/blob/master/LICENSE)

Version history for Django models. Inherit from `VersionedModel` and every save, delete, restore and rollback is recorded as a full snapshot, with a diff, the user who made the change, and a read-only admin to browse it all.

```python
article.title = "Hello, world"
article.save(user=request.user)       # version 2 recorded
article.diff_versions(1, 2)           # {"title": {"from": "Hello", "to": "Hello, world"}}
article.rollback(1)                   # back to version 1 (recorded as version 3)
article.delete()                      # soft delete: hidden, but kept and restorable
Article.objects.restore(pk=article.pk)
```

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Usage](#usage)
  - [Recording versions](#recording-versions)
  - [Who made the change](#who-made-the-change)
  - [Reading history](#reading-history)
  - [Diffs](#diffs)
  - [Audit trail](#audit-trail)
  - [Rollback](#rollback)
  - [Soft delete, restore and hard delete](#soft-delete-restore-and-hard-delete)
  - [Querying soft-deleted rows](#querying-soft-deleted-rows)
  - [Bulk operations](#bulk-operations)
  - [Many-to-many relations](#many-to-many-relations)
  - [Related objects](#related-objects)
  - [Unique fields and soft-deleted rows](#unique-fields-and-soft-deleted-rows)
  - [UUID and string primary keys](#uuid-and-string-primary-keys)
  - [In views and APIs](#in-views-and-apis)
- [Admin](#admin)
- [API reference](#api-reference)
- [How it works](#how-it-works)
- [Limitations](#limitations)
- [Example project](#example-project)
- [Upgrading from 0.1.x](#upgrading-from-01x)
- [Development](#development)

## Features

- **Automatic versions** for create, update, delete, restore and rollback, each a full JSON snapshot of the record, including foreign keys and many-to-many relations.
- **No duplicate versions**: a SHA-256 hash of each snapshot skips saves that changed nothing.
- **Diffs**: every version stores what changed since the previous one, and `diff_versions()` compares any two.
- **User tracking**: pass `user=`, or let it come from the current request.
- **Rollback** to any version, including foreign keys and many-to-many relations.
- **Soft delete and restore**, with `deleted_at` and `deleted_by`, and soft-deleting bulk deletes.
- **Audit trail** of who changed what, and when.
- **Read-only admin** for the whole history, with filters by action and date.
- **Safe under concurrency**: each change and its version are written in one transaction, with a row lock while the version number is assigned.
- Works with **integer, UUID and string primary keys**.

## Requirements

| | Supported | Tested |
|---|---|---|
| Python | 3.8 – 3.14 | 3.8, 3.9, 3.10, 3.11, 3.12, 3.13, 3.14 |
| Django | 3.2 – 6.1 | 3.2, 4.2, 5.0, 5.1, 5.2, 6.0, 6.1 |
| Database | Any Django supports | SQLite and PostgreSQL 16, including concurrent writers; MySQL 8.4 also runs in CI |

Dependencies, installed automatically:

- [`django-crequest`](https://pypi.org/project/django-crequest/), to read the current request's user.
- [`django-admin-rangefilter`](https://pypi.org/project/django-admin-rangefilter/), for the admin's date range filter.

## Installation

```bash
pip install django-versioning
```

Add the apps. `django.contrib.contenttypes` is required.

```python
# settings.py
INSTALLED_APPS = [
    # ...
    "django.contrib.contenttypes",
    "rangefilter",
    "version_control",
]
```

Optionally, add `CrequestMiddleware` so versions record `request.user` automatically when you don't pass `user=`:

```python
MIDDLEWARE = [
    # ...
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "crequest.middleware.CrequestMiddleware",
    # ...
]
```

Create the tables:

```bash
python manage.py migrate
```

## Quick start

```python
# models.py
from django.db import models
from version_control.models import VersionedModel


class Author(VersionedModel):
    name = models.CharField(max_length=100)


class Tag(VersionedModel):
    name = models.CharField(max_length=50, unique=True)


class Article(VersionedModel):
    title = models.CharField(max_length=200, unique=True)
    body = models.TextField(blank=True)
    author = models.ForeignKey(Author, on_delete=models.CASCADE)
    tags = models.ManyToManyField(Tag, blank=True)
```

```bash
python manage.py makemigrations
python manage.py migrate
```

`VersionedModel` is abstract. It adds four fields to your model (`current_version`, `is_deleted`, `deleted_at`, `deleted_by`) and replaces the default manager with one that hides soft-deleted rows.

## Usage

The examples below use the models from the quick start.

### Recording versions

Every `save()` records a version. Nothing else is needed.

```python
ann = Author.objects.create(name="Ann")
article = Article.objects.create(title="Hello", body="First draft", author=ann)
article.current_version                 # 1

article.body = "Second draft"
article.save()
article.current_version                 # 2

article.save()                          # nothing changed: no new version
article.current_version                 # 2
```

`save(update_fields=[...])` works as usual. To record a version yourself, for example after changing rows with `QuerySet.update()`, reload the object and call `save_version()`:

```python
Article.objects.filter(pk=article.pk).update(body="Edited in bulk")
article.refresh_from_db()
article.save_version(user=request.user)
```

### Who made the change

Pass the user explicitly:

```python
article.save(user=request.user)
article.delete(user=request.user)
article.restore(user=request.user)
article.rollback(2, user=request.user)
Article.objects.filter(author=ann).delete(user=request.user)
```

With `CrequestMiddleware` enabled, leave out `user=` and the logged-in user of the current request is used. An explicit `user=` always wins. Outside a request (shell, management commands, Celery tasks), or for anonymous users, the user is `None`.

### Reading history

Versions are `VersionControl` rows:

```python
from version_control.models import VersionControl

VersionControl.objects.latest_version(article)     # newest version, or None
VersionControl.objects.history(article)            # list of all versions, oldest first
VersionControl.objects.get_version(article, 1)     # one version, or None

v = VersionControl.objects.get_version(article, 1)
v.version        # 1
v.action         # "create" | "update" | "delete" | "restore" | "rollback"
v.data           # {"title": "Hello", "body": "First draft", "author": 1, "tags": []}
v.diff           # changes since the previous version (see below)
v.user           # who made the change, or None
v.created_at     # when
v.content_object # the versioned object (None once it is permanently deleted)
```

Snapshots store foreign keys as the related object's primary key, many-to-many fields as a list of primary keys, and files as their name. Dates, decimals and UUIDs are stored as strings. Delete versions store `{"__deleted__": True}`.

To query history across objects, use the ordinary queryset API:

```python
from django.contrib.contenttypes.models import ContentType

ct = ContentType.objects.get_for_model(Article)
VersionControl.objects.filter(content_type=ct, action="delete", created_at__date=today)
VersionControl.objects.filter(user=request.user).order_by("-created_at")[:20]
```

### Diffs

Each version stores what changed since the previous one, using `old` and `new`:

```python
VersionControl.objects.latest_version(article).diff
# {"body": {"old": "First draft", "new": "Second draft"}}
```

`diff_versions(v1, v2)` compares any two versions, using `from` and `to`. Many-to-many fields show what was added and removed:

```python
article.diff_versions(1, 5)
# {
#     "body": {"from": "First draft", "to": "Second draft"},
#     "tags": {"added": [1, 2], "removed": []},
# }
```

It raises `ValueError` if either version doesn't exist.

### Audit trail

```python
for entry in article.audit_trail():
    print(entry["version"], entry["action"], entry["changed_by"], entry["changed_at"])
    print(entry["changes"])

# 1 create alice 2026-10-23 17:49:11+00:00
# {"title": {"from": None, "to": "Hello"}, "body": {"from": None, "to": "First draft"}, ...}
# 2 update alice 2026-10-23 17:50:02+00:00
# {"body": {"from": "First draft", "to": "Second draft"}}
```

The creation is always included; later versions that changed nothing (such as a rollback to the current state) are left out. `changed_by` is the user's username, or `None`.

### Rollback

```python
article.rollback(1)
article.rollback(1, user=request.user)
```

Rollback restores plain fields, foreign keys and many-to-many relations from the snapshot, and records a new `rollback` version, so history is never rewritten. Fields added to the model after that snapshot was taken keep their current value.

Rolling back to a delete version soft-deletes the record; rolling a soft-deleted record back to an earlier version restores it. A missing version raises `ValueError("Version not found")`.

### Soft delete, restore and hard delete

```python
article.delete()                         # soft delete
article.is_deleted                       # True
article.deleted_at                       # timestamp
article.deleted_by                       # user (explicit or from the request)

article.restore()                        # undo the soft delete
Article.objects.restore(pk=article.pk)   # same, by primary key; returns the object or None

article.delete(hard=True)                # permanent: the row is removed
```

A soft delete records a `delete` version and keeps the row. Deleting an already soft-deleted record does nothing. A hard delete records a `delete` version, then removes the row; its history stays in `VersionControl`. Restoring a record that isn't deleted does nothing.

### Querying soft-deleted rows

`Article.objects` hides soft-deleted rows. Other ways in:

```python
Article.objects.all()                    # live rows only
Article.objects.deleted()                # soft-deleted rows only
Article.objects.all_with_deleted()       # everything

from version_control.managers import include_deleted

with include_deleted():                  # every Article.objects query in this block
    Article.objects.filter(title="Hello").exists()     # sees soft-deleted rows too
```

`include_deleted()` is safe with threads and async code.

### Bulk operations

```python
Article.objects.filter(author=ann).delete()              # soft-deletes each row, records a version for each
Article.objects.filter(author=ann).delete(user=request.user)
Article.objects.deleted().hard_delete()                  # permanently deletes each row, recording a delete version
```

Both return `(count, {"app.Article": count})`, like Django's `QuerySet.delete()`. They go through each object's `delete()`, so they cost a few queries per row. Rows that are already soft-deleted are skipped by `delete()`. There is deliberately no `Article.objects.delete()`, so a whole table can't be deleted by accident.

### Many-to-many relations

Changes to many-to-many fields record a version of the object whose field changed, from either side of the relation:

```python
article.tags.add(django_tag)             # versions article
django_tag.article_set.add(article)      # also versions article
article.tags.remove(django_tag)
article.tags.clear()
article.tags.set([python_tag])
```

For symmetrical relations (`ManyToManyField("self")`), both objects are versioned. On `add()`, the other object's version is written when the transaction commits, because Django inserts the mirror row after sending its signal.

### Related objects

When you save an object, related `VersionedModel` objects that are already loaded in memory (a cached foreign key, or a prefetched many-to-many) are versioned too, if they changed. Related objects that aren't loaded are never fetched just for this, because they can't have unsaved changes.

### Unique fields and soft-deleted rows

A soft-deleted row still occupies its unique values in the database. Model forms and `full_clean()` take it into account, so the user gets a validation error instead of a database `IntegrityError`:

```python
article.delete()
form = ArticleForm({"title": article.title, "author": ann.pk})
form.is_valid()      # False
form.errors          # {"title": ["Article with this Title already exists."]}
```

This covers `unique=True`, `unique_together`, `UniqueConstraint` and `unique_for_date`. Restore the old row, or give the new one a different value.

### UUID and string primary keys

These need nothing special:

```python
class Page(VersionedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slug = models.SlugField(unique=True)
```

### In views and APIs

With `CrequestMiddleware` enabled, ordinary view code records the user automatically:

```python
@login_required
@require_POST
def rollback_article(request, pk, version):
    article = get_object_or_404(Article.objects.all_with_deleted(), pk=pk)
    try:
        article.rollback(version)          # recorded with request.user
    except ValueError:
        raise Http404("No such version")
    return JsonResponse({"current_version": article.current_version})
```

The [example project](https://github.com/deepak7448/django-versioning/tree/master/examples) has a complete JSON API: history, audit trail, diff, update, rollback, delete and restore.

## Admin

### Version history

Adding `version_control` to `INSTALLED_APPS` registers a read-only **Version Control** admin. It lists every version with:

- a link to the versioned object, labelled "(deleted)" or "Deleted (ID: …)" once it's gone,
- the version number, action and user,
- filters by action and date range, plus a date hierarchy,
- search by object ID, data hash and username.

Versions can't be added, changed or deleted from the admin; staff users need the "view version control" permission to see it.

To show the history of one object, filter by content type and object ID:

```
/admin/version_control/versioncontrol/?content_type=<content type id>&object_id=<pk>
```

### Your versioned models

By default, a `ModelAdmin` for a `VersionedModel` hides soft-deleted rows, and its "Delete selected" action soft-deletes and records a version for each row. To list soft-deleted rows, restore them, and link to each row's history:

```python
from django.contrib import admin, messages
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from django.utils.html import format_html


class VersionedAdmin(admin.ModelAdmin):
    list_filter = ("is_deleted",)
    readonly_fields = ("current_version", "is_deleted", "deleted_at", "deleted_by")
    actions = ["restore_selected", "hard_delete_selected"]

    def get_queryset(self, request):
        return self.model.objects.all_with_deleted()

    @admin.display(description="History")
    def history(self, obj):
        ct = ContentType.objects.get_for_model(obj)
        url = reverse("admin:version_control_versioncontrol_changelist")
        return format_html('<a href="{}?content_type={}&object_id={}">v{}</a>', url, ct.pk, obj.pk, obj.current_version)

    @admin.action(description="Restore selected soft-deleted rows")
    def restore_selected(self, request, queryset):
        for obj in queryset.filter(is_deleted=True):
            obj.restore(user=request.user)

    @admin.action(description="Permanently delete selected rows")
    def hard_delete_selected(self, request, queryset):
        count, _ = queryset.hard_delete(user=request.user)
        self.message_user(request, f"Permanently deleted {count} row(s).", messages.WARNING)


@admin.register(Article)
class ArticleAdmin(VersionedAdmin):
    list_display = ("title", "author", "is_deleted", "history")
```

## API reference

### `VersionedModel`

| Member | Description |
|---|---|
| `current_version` | Number of the latest version. |
| `is_deleted`, `deleted_at`, `deleted_by` | Soft-delete state. |
| `save(*args, user=None, **kwargs)` | Save and record a `create` or `update` version (skipped if nothing changed). |
| `delete(user=None, hard=False)` | Soft delete, or permanently delete with `hard=True`. Records a `delete` version. |
| `restore(user=None)` | Undo a soft delete. Records a `restore` version. |
| `rollback(version_number, user=None)` | Restore the state of a version. Records a `rollback` version. Raises `ValueError` if the version doesn't exist. |
| `diff_versions(v1, v2)` | Changes between two versions. Raises `ValueError` if either doesn't exist. |
| `audit_trail()` | List of `{version, action, changed_by, changed_at, changes}`. |
| `save_version(user=None, tombstone=False, action="update")` | Record a version of the current state yourself. |

### `VersionedModel.objects` (`VersionedModelManager`)

| Member | Description |
|---|---|
| `all()`, `filter()`, … | Live rows only. |
| `deleted()` | Soft-deleted rows only. |
| `all_with_deleted()` | All rows. |
| `restore(pk, user=None)` | Restore by primary key; returns the object, or `None` if it doesn't exist. |

Querysets from this manager also have:

| Member | Description |
|---|---|
| `delete(user=None)` | Soft-delete each live row. Returns `(count, {label: count})`. |
| `hard_delete(user=None)` | Permanently delete each row, recording a delete version. Returns `(count, {label: count})`. |

### `version_control.managers.include_deleted()`

Context manager: `VersionedModel` managers include soft-deleted rows inside the block.

### `VersionControl`

| Field | Description |
|---|---|
| `content_type`, `object_id` | The versioned object (`object_id` is text). |
| `content_object` | Generic relation to the versioned object. |
| `version` | Version number, unique per object. |
| `action` | `create`, `update`, `delete`, `restore` or `rollback`. |
| `data` | Full JSON snapshot. |
| `data_hash` | SHA-256 of the snapshot. |
| `diff` | `{"field": {"old": …, "new": …}}` since the previous version. |
| `user` | Who made the change, or `None`. |
| `created_at` | When the version was recorded. |

`VersionControl.objects` adds `latest_version(obj)`, `history(obj)` and `get_version(obj, number)`.

## How it works

- `save()`, `delete()`, `restore()` and `rollback()` write the record and its `VersionControl` row in one transaction. If either fails, both are rolled back.
- The snapshot is serialised to JSON and hashed. If the hash matches the previous version, no version is written.
- When a version is written from outside those methods (for example a many-to-many change), the row is locked with `SELECT … FOR UPDATE` before the next version number is assigned, so concurrent writers can't collide. `(content_type, object_id, version)` is unique.
- An update costs 4 queries on top of Django's own `UPDATE`: read the last version, read many-to-many primary keys (one query per many-to-many field), insert the version, and update `current_version`. A save that changes nothing stops after the two reads.
- The internal `current_version` update uses `QuerySet.update()`, so your `post_save` handlers run once per save.

## Limitations

- `QuerySet.update()`, `bulk_create()`, `bulk_update()` and raw SQL bypass `save()`, so they record no versions. Reload the objects and call `save_version()` if you need one.
- When a parent row is permanently deleted, Django's cascade removes child rows without calling their `delete()`, so the children get no delete version. Soft-deleting a parent doesn't cascade at all.
- Each snapshot stores the whole record, so history grows with the number of changes times the record size.
- `django-crequest` stores the current request per thread. Under async (ASGI) views, pass `user=` explicitly.
- Rollback restores the foreign keys stored in a snapshot. If a related row has since been permanently deleted, the database rejects the rollback with an integrity error.

## Example project

[`examples/`](https://github.com/deepak7448/django-versioning/tree/master/examples) contains a runnable blog with a step-by-step demo of every feature, the admin setup above and a JSON API:

```bash
git clone https://github.com/deepak7448/django-versioning.git
cd django-versioning/examples
pip install -e ..
python manage.py migrate
python manage.py demo
```

## Upgrading from 0.1.x

1. Run `python manage.py migrate`. `VersionControl.object_id` becomes a text column.
2. `QuerySet.delete()` on versioned models now soft-deletes, including the admin's "Delete selected" action. Use `hard_delete()` where you relied on permanent deletion.
3. Snapshots are now normalised before hashing, so the first save of an existing object with date, time, decimal or UUID fields records one extra version.

See the [changelog](https://github.com/deepak7448/django-versioning/blob/master/CHANGELOG.md) for everything that changed.

## Development

Development needs Python 3.9 or newer, because building the package from source needs setuptools 77+. Installing the published wheel works on Python 3.8.

```bash
git clone https://github.com/deepak7448/django-versioning.git
cd django-versioning
python3 -m venv venv
venv/bin/pip install -e . build twine
venv/bin/python runtests.py                     # package tests (SQLite in memory)
cd examples && ../venv/bin/python manage.py test blog   # example project tests
```

To run the package tests against another database, set `DB_ENGINE` and the connection variables (`DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`). The concurrency tests only run on databases with row locks, such as PostgreSQL and MySQL:

## License

MIT. See [LICENSE](https://github.com/deepak7448/django-versioning/blob/master/LICENSE).
