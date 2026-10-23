"""
python manage.py demo [--keep]

Walks through every django-versioning feature and prints what happens.
The blog tables are cleared first. By default everything is rolled back at
the end; pass --keep to leave the data in place and browse it in the admin.
"""
import json

from crequest.middleware import CrequestMiddleware
from django import forms
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.test import RequestFactory

from blog.models import Article, Author, Page, Tag
from version_control.managers import include_deleted
from version_control.models import VersionControl


class ArticleForm(forms.ModelForm):
    class Meta:
        model = Article
        fields = ["title", "author"]


class Command(BaseCommand):
    help = "Demonstrate every django-versioning feature."

    def add_arguments(self, parser):
        parser.add_argument("--keep", action="store_true", help="Keep the demo data instead of rolling back.")

    def handle(self, *args, keep=False, **options):
        with transaction.atomic():
            self.reset()
            self.run()
            if not keep:
                transaction.set_rollback(True)
        self.stdout.write(self.style.SUCCESS("\nDone." + ("" if keep else " (rolled back; use --keep to keep the data)")))

    # helpers

    def step(self, title):
        self.stdout.write(self.style.MIGRATE_HEADING(f"\n== {title}"))

    def show(self, label, value):
        if not isinstance(value, str):
            value = json.dumps(value, default=str)
        self.stdout.write(f"   {label}: {value}")

    def versions(self, obj):
        return [(v.version, v.action) for v in VersionControl.objects.history(obj)]

    def reset(self):
        # _base_manager is a plain manager: these deletes bypass versioning on purpose
        for model in (Article, Tag, Author, Page):
            model._base_manager.all().delete()
        VersionControl.objects.filter(content_type__app_label="blog").delete()

    # walkthrough

    def run(self):
        User = get_user_model()
        alice = User.objects.get_or_create(username="alice")[0]
        bob = User.objects.get_or_create(username="bob")[0]

        self.step("1. Create: every save of a VersionedModel records a version")
        ann = Author.objects.create(name="Ann")
        article = Article(title="Hello", body="First draft", author=ann)
        article.save(user=alice)
        self.show("versions", self.versions(article))
        self.show("current_version", article.current_version)

        self.step("2. Update: a new version with a precomputed diff")
        article.body = "Second draft"
        article.save(user=alice)
        self.show("versions", self.versions(article))
        self.show("stored diff", VersionControl.objects.latest_version(article).diff)

        self.step("3. Saving without changes records nothing")
        article.save(user=alice)
        self.show("versions", self.versions(article))

        self.step("4. User from the current request (CrequestMiddleware)")
        request = RequestFactory().get("/")
        request.user = bob
        CrequestMiddleware.set_request(request)  # the middleware does this for every request
        try:
            article.title = "Hello, world"
            article.save()  # no user= needed
        finally:
            CrequestMiddleware.del_request()
        self.show("latest version user", str(VersionControl.objects.latest_version(article).user))

        self.step("5. Many-to-many changes are versioned, from either side")
        django_tag = Tag.objects.create(name="django")
        python_tag = Tag.objects.create(name="python")
        article.tags.add(django_tag)
        python_tag.articles.add(article)  # reverse side also versions the article
        self.show("tags in latest snapshot", VersionControl.objects.latest_version(article).data["tags"])
        self.show("versions", self.versions(article))

        self.step("6. Reading history")
        self.show("latest_version", str(VersionControl.objects.latest_version(article)))
        self.show("get_version(article, 1).data['body']", VersionControl.objects.get_version(article, 1).data["body"])
        self.show("history", [str(v) for v in VersionControl.objects.history(article)])

        self.step("7. Diff any two versions")
        self.show("diff_versions(1, 5)", article.diff_versions(1, 5))

        self.step("8. Audit trail: who changed what, and when")
        for entry in article.audit_trail():
            self.show(f"v{entry['version']} {entry['action']} by {entry['changed_by']}", entry["changes"])

        self.step("9. Rollback to an earlier version (fields, foreign keys and M2M)")
        article.rollback(1, user=alice)
        self.show("title / body / tags", [article.title, article.body, list(article.tags.values_list("name", flat=True))])
        self.show("versions", self.versions(article))

        self.step("10. Soft delete: hidden from Article.objects, kept in the table")
        article.delete(user=alice)
        self.show("Article.objects.filter(pk=...).exists()", Article.objects.filter(pk=article.pk).exists())
        self.show("Article.objects.deleted().count()", Article.objects.deleted().count())
        self.show("Article.objects.all_with_deleted().count()", Article.objects.all_with_deleted().count())
        with include_deleted():
            self.show("inside include_deleted(): Article.objects.count()", Article.objects.count())
        self.show("deleted_by / deleted_at", [str(article.deleted_by), article.deleted_at])
        self.show("tombstone", VersionControl.objects.latest_version(article).data)

        self.step("11. Soft-deleted rows still count for uniqueness")
        form = ArticleForm({"title": article.title, "author": ann.pk})
        self.show("form.is_valid()", form.is_valid())
        self.show("form.errors", form.errors.get_json_data())

        self.step("12. Restore")
        article.restore(user=alice)  # or Article.objects.restore(pk=article.pk, user=alice)
        self.show("is_deleted", article.is_deleted)
        self.show("versions", self.versions(article))

        self.step("13. Rolling back to a delete version soft-deletes again")
        delete_version = VersionControl.objects.filter(
            content_type__model="article", object_id=article.pk, action="delete"
        ).first().version
        article.rollback(delete_version, user=alice)
        self.show("is_deleted", article.is_deleted)
        Article.objects.restore(pk=article.pk, user=alice)

        self.step("14. Bulk operations on querysets")
        for i in range(3):
            Article.objects.create(title=f"Bulk {i}", author=ann)
        self.show("filter(...).delete(user=alice)  (soft)", Article.objects.filter(title__startswith="Bulk").delete(user=alice))
        self.show("deleted().hard_delete()  (permanent)", Article.objects.deleted().hard_delete(user=alice))

        self.step("15. Hard delete: the row is gone, the history stays")
        pk = article.pk
        article.delete(user=alice, hard=True)
        self.show("row exists", Article.objects.all_with_deleted().filter(pk=pk).exists())
        remaining = VersionControl.objects.filter(content_type__model="article", object_id=pk).order_by("version")
        self.show("versions kept", [(v.version, v.action) for v in remaining])

        self.step("16. UUID primary keys work the same way")
        page = Page.objects.create(slug="about", content="v1")
        page.content = "v2"
        page.save()
        page.rollback(1)
        self.show("pk", str(page.pk))
        self.show("content after rollback(1)", page.content)
        self.show("versions", self.versions(page))
