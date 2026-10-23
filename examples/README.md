# django-versioning example project

A small blog that uses every feature of django-versioning. It runs straight from a checkout of this repository; `manage.py` puts the repository root on the Python path, so the package doesn't need to be installed.

## Run it

```bash
cd examples
pip install "Django>=3.2" django-crequest django-admin-rangefilter   # or: pip install -e ..
python manage.py migrate
python manage.py demo            # walk through every feature (rolled back afterwards)
python manage.py demo --keep     # same, but keep the data to browse in the admin
python manage.py createsuperuser
python manage.py runserver
```

Then open <http://127.0.0.1:8000/admin/>.

`demo` clears the blog tables before it starts.

## What's where

| File | Shows |
|---|---|
| `example_project/settings.py` | `INSTALLED_APPS` and `MIDDLEWARE` setup, including `CrequestMiddleware` |
| `blog/models.py` | `VersionedModel` subclasses with a foreign key, a many-to-many field, a unique field and a UUID primary key |
| `blog/management/commands/demo.py` | Every feature, step by step: create, update, request user, many-to-many, history, diffs, audit trail, rollback, soft delete, uniqueness, restore, bulk delete, hard delete, UUID keys |
| `blog/admin.py` | A reusable `VersionedAdmin`: lists soft-deleted rows, restore and permanent-delete actions, and a link from each row to its version history |
| `blog/views.py` | A JSON API: history, audit trail, diff, update, rollback, delete and restore, with the user recorded from the request |
| `blog/tests.py` | Tests for the views, the admin and the demo command (`python manage.py test blog`) |

## The JSON API

All endpoints need a logged-in user (log in through the admin first).

| Method | URL | Does |
|---|---|---|
| GET | `/articles/<pk>/history/` | Every version with its data |
| GET | `/articles/<pk>/audit/` | Audit trail |
| GET | `/articles/<pk>/diff/<v1>/<v2>/` | Changes between two versions |
| POST | `/articles/<pk>/update/` | Update `title`/`body` from a JSON body |
| POST | `/articles/<pk>/rollback/<version>/` | Roll back |
| POST | `/articles/<pk>/delete/` | Soft delete (`?hard=1` to delete permanently) |
| POST | `/articles/<pk>/restore/` | Restore a soft-deleted article |
