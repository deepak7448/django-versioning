# Django Version Control

A lightweight **relational version control system** for Django models. It records snapshots of model state (including FK and M2M relations), computes hashes to skip duplicate snapshots, stores human-readable diffs, supports soft-delete/restore, rollback, and generates audit trails.

---

## ✨ Features

* Automatic versioning of models that inherit `VersionedModel`.
* Tracks **create, update, delete, restore, rollback** actions.
* Stores **full JSON snapshot** of records in `VersionControl`.
* Computes **SHA-256 hash** of snapshots to avoid duplicate saves.
* Prepares human-readable **diffs** between versions.
* Supports **soft delete** and **restore** with user/timestamp metadata.
* **Rollback** a record (and related `VersionedModel` objects) to any previous version.
* **Audit trail** to see who changed what and when.
* Handles nested **ForeignKey** and **ManyToMany** relationships for `VersionedModel` objects.

---

## 🚀 Installation

Install the package and (optionally) django-crequest to allow automatic `request.user` detection, plus django-admin-rangefilter to filter history by date in admin:

```bash
pip install django-version-control
pip install django-crequest
pip install django-admin-rangefilter
```

Add the app to `INSTALLED_APPS` in your `settings.py`:

```python
INSTALLED_APPS = [
    ...
    "django.contrib.contenttypes",  # required
    "version_control",              
    "rangefilter",                  # for DateRangeFilter in admin
]
```

If you installed `django-crequest`, add its middleware to your `MIDDLEWARE` list so the package can pick up the current `request.user` automatically when you don't explicitly pass `user=` to save/rollback/delete calls.

```python
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",

    # Add crequest.middleware.CrequestMiddleware so VersionedModel can
    # obtain the current request and user automatically.
    "crequest.middleware.CrequestMiddleware",

    "django.contrib.auth.middleware.AuthenticationMiddleware",
    ...
]
```

> Note: `crequest.middleware.CrequestMiddleware` is **optional**, but recommended. If present, `VersionedModel` will attempt to use `CrequestMiddleware.get_request().user` when `user` is not provided to `save()`, `delete()`, `restore()`, or `rollback()`.

Run migrations:

```bash
python manage.py makemigrations
python manage.py migrate
```

---

## ⚡ Quick Usage

### 1. Inherit from `VersionedModel`

```python
from django.db import models
from version_control.models import VersionedModel

class Tag(VersionedModel):
    name = models.CharField(max_length=100)

class Article(VersionedModel):
    title = models.CharField(max_length=255)
    content = models.TextField()
    tags = models.ManyToManyField(Tag, blank=True)
```

### 2. Saving Versions

A version snapshot is created automatically when you call `.save()` on a `VersionedModel`. You can pass the user explicitly:

```python
article.save(user=request.user)
```

If you omit `user=` and `crequest.middleware.CrequestMiddleware` is enabled, the package will try to resolve the current user from the active request.

### 3. Soft Delete & Restore

```python
article.delete(user=request.user)  # soft delete and record a tombstone snapshot
article.restore(user=request.user) # restore from soft-delete
```

To permanently delete a record (hard delete):

```python
article.delete(user=request.user, hard=True)
```

### 4. Rollback

```python
article.rollback(version_number=2, user=request.user)
```

### 5. Diffing Versions

```python
diff = article.diff_versions(1, 3)
# Example: {"title": {"from": "First Post", "to": "Updated Post"}}
```

### 6. Audit Trail

```python
history = article.audit_trail()
for entry in history:
    print(entry["version"], entry["changed_by"], entry["changed_at"]) 
    print(entry["changes"]) 
```

---

## 🛠️ Implementation Notes

* The package uses Django's `ContentType` framework. Ensure `django.contrib.contenttypes` is enabled.
* `crequest.middleware.CrequestMiddleware` is helpful for automatically populating `user` when saving versions from request-handling code; without it you should pass `user=` explicitly when you want to track who made a change.
* `django-admin-rangefilter` enhances the admin by letting you filter version history by created\_at date range.
* For large datasets, consider indexing `VersionControl` fields you query often (such as `object_id`, `content_type` and `data_hash`).

---

## ✅ Example Workflow

```python
tag = Tag.objects.create(name="django")
article = Article.objects.create(title="Intro", content="Hello!", user=request.user)
article.tags.add(tag)
article.save(user=request.user)

# Update
article.content = "Updated content"
article.save(user=request.user)

# See audit trail
print(article.audit_trail())
```

---

## 📌 Compatibility

* Designed to work with Django 3.2+.
* Works with SQLite, Postgres, and MySQL (JSON fields will use Django's JSONField abstraction).

---