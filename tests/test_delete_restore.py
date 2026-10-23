from django.contrib.auth import get_user_model
from django.test import TestCase

from version_control.models import VersionControl
from tests.testapp.models import Post
from tests.utils import make_post, request_user


class SoftDeleteTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("deleter")
        self.post = make_post()

    def test_soft_delete_flags_record(self):
        self.post.delete(user=self.user)

        self.post.refresh_from_db()
        self.assertTrue(self.post.is_deleted)
        self.assertIsNotNone(self.post.deleted_at)
        self.assertEqual(self.post.deleted_by, self.user)

    def test_soft_delete_hides_from_default_manager(self):
        self.post.delete()

        self.assertFalse(Post.objects.filter(pk=self.post.pk).exists())
        self.assertTrue(Post.objects.deleted().filter(pk=self.post.pk).exists())
        self.assertTrue(Post.objects.all_with_deleted().filter(pk=self.post.pk).exists())

    def test_soft_delete_records_tombstone(self):
        self.post.delete(user=self.user)

        version = VersionControl.objects.latest_version(self.post)
        self.assertEqual((version.version, version.action), (2, "delete"))
        self.assertEqual(version.data, {"__deleted__": True})
        self.assertEqual(version.user, self.user)
        self.assertEqual(self.post.current_version, 2)

    def test_soft_delete_records_request_user_when_none_passed(self):
        with request_user(self.user):
            self.post.delete()

        self.post.refresh_from_db()
        self.assertEqual(self.post.deleted_by, self.user)

    def test_deleting_already_deleted_record_is_a_no_op(self):
        self.post.delete()
        self.post.delete()

        self.assertEqual(VersionControl.objects.latest_version(self.post).version, 2)


class HardDeleteTests(TestCase):
    def test_hard_delete_removes_row_and_keeps_tombstone(self):
        post = make_post()
        pk = post.pk

        post.delete(hard=True)

        self.assertFalse(Post.objects.all_with_deleted().filter(pk=pk).exists())
        last = VersionControl.objects.filter(content_type__model="post", object_id=pk).order_by("-version").first()
        self.assertEqual((last.version, last.action, last.data), (2, "delete", {"__deleted__": True}))

    def test_hard_delete_of_soft_deleted_record(self):
        post = make_post()
        pk = post.pk
        post.delete()

        post.delete(hard=True)

        self.assertFalse(Post.objects.all_with_deleted().filter(pk=pk).exists())


class RestoreTests(TestCase):
    def setUp(self):
        self.post = make_post()
        self.post.delete()

    def test_restore_clears_deleted_flags(self):
        self.post.restore()

        self.post.refresh_from_db()
        self.assertFalse(self.post.is_deleted)
        self.assertIsNone(self.post.deleted_at)
        self.assertIsNone(self.post.deleted_by)

    def test_restore_records_full_snapshot(self):
        self.post.restore()

        version = VersionControl.objects.latest_version(self.post)
        self.assertEqual((version.version, version.action), (3, "restore"))
        self.assertEqual(version.data["title"], "p")

    def test_restore_of_live_record_is_a_no_op(self):
        self.post.restore()
        self.post.restore()

        self.assertEqual(VersionControl.objects.latest_version(self.post).version, 3)

    def test_manager_restore(self):
        restored = Post.objects.restore(pk=self.post.pk)

        self.assertEqual(restored.pk, self.post.pk)
        self.assertFalse(restored.is_deleted)
        self.assertTrue(Post.objects.filter(pk=self.post.pk).exists())

    def test_manager_restore_missing_pk_returns_none(self):
        self.assertIsNone(Post.objects.restore(pk=999999))

    def test_manager_restore_live_record_returns_it_unchanged(self):
        live = make_post(title="live")

        self.assertEqual(Post.objects.restore(pk=live.pk), live)
        self.assertEqual(VersionControl.objects.latest_version(live).version, 1)


class QuerySetDeleteTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("deleter")
        self.post = make_post()

    def test_queryset_delete_soft_deletes(self):
        Post.objects.filter(pk=self.post.pk).delete()

        self.assertFalse(Post.objects.filter(pk=self.post.pk).exists())
        self.assertTrue(Post.objects.deleted().filter(pk=self.post.pk).exists())

    def test_queryset_delete_records_delete_version_and_user(self):
        Post.objects.filter(pk=self.post.pk).delete(user=self.user)

        version = VersionControl.objects.latest_version(self.post)
        self.assertEqual((version.action, version.user), ("delete", self.user))

    def test_queryset_delete_return_value(self):
        make_post(title="q", author=self.post.author)

        self.assertEqual(Post.objects.all().delete(), (2, {"testapp.Post": 2}))

    def test_queryset_delete_skips_already_deleted_rows(self):
        self.post.delete()

        self.assertEqual(Post.objects.all_with_deleted().delete(), (0, {"testapp.Post": 0}))
        self.assertEqual(VersionControl.objects.latest_version(self.post).version, 2)

    def test_queryset_hard_delete_removes_rows_and_keeps_tombstones(self):
        pk = self.post.pk

        self.assertEqual(Post.objects.filter(pk=pk).hard_delete(), (1, {"testapp.Post": 1}))

        self.assertFalse(Post.objects.all_with_deleted().filter(pk=pk).exists())
        self.assertTrue(VersionControl.objects.filter(content_type__model="post", object_id=pk, action="delete").exists())

    def test_manager_has_no_bulk_delete_shortcut(self):
        self.assertFalse(hasattr(Post.objects, "delete"))
        self.assertFalse(hasattr(Post.objects, "hard_delete"))
