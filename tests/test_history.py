from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from version_control.models import VersionControl
from tests.testapp.models import Author, Post, Tag
from tests.utils import make_post


class ManagerTests(TestCase):
    def setUp(self):
        self.post = make_post(title="v1")
        self.post.title = "v2"
        self.post.save()

    def test_latest_version(self):
        self.assertEqual(VersionControl.objects.latest_version(self.post).version, 2)

    def test_history_is_ascending(self):
        self.assertEqual([v.version for v in VersionControl.objects.history(self.post)], [1, 2])

    def test_get_version(self):
        self.assertEqual(VersionControl.objects.get_version(self.post, 1).data["title"], "v1")

    def test_get_missing_version_returns_none(self):
        self.assertIsNone(VersionControl.objects.get_version(self.post, 99))

    def test_latest_version_for_object_without_history(self):
        VersionControl.objects.all().delete()

        self.assertIsNone(VersionControl.objects.latest_version(self.post))


class DiffVersionsTests(TestCase):
    def setUp(self):
        self.post = make_post(title="v1")
        self.tag = Tag.objects.create(name="t")
        self.post.tags.add(self.tag)  # v2
        self.post.title = "v2"
        self.post.save()  # v3

    def test_diff_versions_reports_field_and_m2m_changes(self):
        self.assertEqual(
            self.post.diff_versions(1, 3),
            {"title": {"from": "v1", "to": "v2"}, "tags": {"added": [self.tag.pk], "removed": []}},
        )

    def test_diff_versions_reversed(self):
        self.assertEqual(
            self.post.diff_versions(3, 1),
            {"title": {"from": "v2", "to": "v1"}, "tags": {"added": [], "removed": [self.tag.pk]}},
        )

    def test_diff_of_same_version_is_empty(self):
        self.assertEqual(self.post.diff_versions(2, 2), {})

    def test_diff_against_tombstone(self):
        self.post.delete()  # v4

        diff = self.post.diff_versions(3, 4)

        self.assertEqual(diff["__deleted__"], {"from": None, "to": True})
        self.assertEqual(diff["title"], {"from": "v2", "to": None})

    def test_diff_with_missing_versions_raises(self):
        with self.assertRaisesMessage(ValueError, "[98, 99]"):
            self.post.diff_versions(98, 99)

    def test_diff_uses_one_query(self):
        with CaptureQueriesContext(connection) as ctx:
            self.post.diff_versions(1, 3)

        self.assertEqual(len(ctx), 1)


class AuditTrailTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("editor")
        self.post = make_post(title="v1")
        self.post.title = "v2"
        self.post.save(user=self.user)

    def test_audit_trail_includes_creation(self):
        self.assertEqual([entry["version"] for entry in self.post.audit_trail()], [1, 2])

    def test_audit_trail_reports_user_and_changes(self):
        entry = self.post.audit_trail()[1]

        self.assertEqual(entry["action"], "update")
        self.assertEqual(entry["changed_by"], "editor")
        self.assertEqual(entry["changes"], {"title": {"from": "v1", "to": "v2"}})

    def test_audit_trail_without_user(self):
        self.assertIsNone(self.post.audit_trail()[0]["changed_by"])

    def test_audit_trail_covers_delete_and_restore(self):
        self.post.delete()
        self.post.restore()

        self.assertEqual([e["action"] for e in self.post.audit_trail()], ["create", "update", "delete", "restore"])

    def test_audit_trail_skips_versions_without_changes(self):
        self.post.rollback(2)  # forced version identical to v2

        self.assertEqual([e["version"] for e in self.post.audit_trail()], [1, 2])


class PerformanceTests(TestCase):
    def _save_query_count(self, n_tags):
        author = Author.objects.create(name="Ann")
        post = Post.objects.create(title=f"p{n_tags}", author=author)
        post.tags.set([Tag.objects.create(name=f"t{i}") for i in range(n_tags)])
        post = Post.objects.get(pk=post.pk)
        post.title += "!"
        with CaptureQueriesContext(connection) as ctx:
            post.save()
        return len(ctx)

    def test_save_query_count_does_not_grow_with_related_objects(self):
        self.assertEqual(self._save_query_count(1), self._save_query_count(5))


class MigrationTests(TestCase):
    def test_no_missing_migrations(self):
        out = StringIO()
        try:
            call_command("makemigrations", "version_control", "--check", "--dry-run", stdout=out)
        except SystemExit:
            self.fail(f"Missing migrations:\n{out.getvalue()}")


class QueryCountTests(TestCase):
    """Pin the cost of each operation; inside TestCase the outer transaction shows up as SAVEPOINT/RELEASE."""

    def setUp(self):
        # Fresh instance, so no related object is cached on it
        self.post = Post.objects.get(pk=make_post(title="v1").pk)

    def test_update_queries(self):
        # SAVEPOINT, UPDATE row, SELECT last version, SELECT m2m pks, INSERT version, UPDATE current_version, RELEASE
        self.post.title = "v2"
        with self.assertNumQueries(7):
            self.post.save()

    def test_soft_delete_queries(self):
        # SAVEPOINT, UPDATE row, SELECT last version, INSERT version, UPDATE current_version, RELEASE
        with self.assertNumQueries(6):
            self.post.delete()

    def test_unchanged_save_queries(self):
        # SAVEPOINT, UPDATE row, SELECT last version, SELECT m2m pks, RELEASE
        with self.assertNumQueries(5):
            self.post.save()

    def test_unchanged_cached_related_object_costs_one_query(self):
        self.post.author  # cache it
        # the update above plus one SELECT of the author's last version
        self.post.title = "v2"
        with self.assertNumQueries(8):
            self.post.save()

    def test_m2m_change_locks_before_writing(self):
        tag = Tag.objects.create(name="t")
        with CaptureQueriesContext(connection) as ctx:
            self.post.tags.add(tag)

        self.assertEqual(VersionControl.objects.latest_version(self.post).data["tags"], [tag.pk])
        # SQLite ignores FOR UPDATE, so look for the lock SELECT followed by a re-read of the last version
        selects = [q["sql"] for q in ctx if q["sql"].startswith("SELECT")]
        self.assertTrue(any('FROM "testapp_post" WHERE "testapp_post"."id"' in sql for sql in selects))


class ContentObjectTests(TestCase):
    def test_content_object_returns_soft_deleted_object(self):
        post = make_post()
        post.delete()

        self.assertEqual(VersionControl.objects.latest_version(post).content_object, post)

    def test_content_object_is_none_after_hard_delete(self):
        post = make_post()
        pk = post.pk
        post.delete(hard=True)

        version = VersionControl.objects.filter(content_type__model="post", object_id=pk).last()
        self.assertIsNone(version.content_object)


class ManualSaveVersionTests(TestCase):
    def test_save_version_after_queryset_update(self):
        post = make_post(title="v1")
        Post.objects.filter(pk=post.pk).update(title="v2")

        post.refresh_from_db()
        post.save_version()

        self.assertEqual(VersionControl.objects.latest_version(post).data["title"], "v2")
