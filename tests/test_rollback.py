import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from version_control.models import VersionControl
from tests.testapp.models import Author, Post, Tag


class RollbackTests(TestCase):
    def setUp(self):
        self.author = Author.objects.create(name="Ann")
        self.other = Author.objects.create(name="Bob")
        self.post = Post.objects.create(title="v1", author=self.author)

    def test_rollback_restores_plain_fields(self):
        self.post.title = "v2"
        self.post.save()

        self.post.rollback(1)

        self.assertEqual(Post.objects.get(pk=self.post.pk).title, "v1")

    def test_rollback_restores_foreign_key(self):
        self.post.author = self.other
        self.post.save()

        self.post.rollback(1)

        self.assertEqual(Post.objects.get(pk=self.post.pk).author, self.author)

    def test_rollback_restores_datetime_as_datetime(self):
        original = self.post.published_at
        self.post.published_at = original + datetime.timedelta(days=1)
        self.post.save()

        self.post.rollback(1)

        self.assertIsInstance(self.post.published_at, datetime.datetime)
        self.assertEqual(Post.objects.get(pk=self.post.pk).published_at, original)

    def test_rollback_restores_file_field(self):
        self.post.attachment = "attachments/a.txt"
        self.post.save()
        self.post.attachment = "attachments/b.txt"
        self.post.save()

        self.post.rollback(2)

        self.assertEqual(Post.objects.get(pk=self.post.pk).attachment.name, "attachments/a.txt")

    def test_rollback_restores_many_to_many(self):
        tag = Tag.objects.create(name="django")
        self.post.tags.add(tag)  # version 2
        self.post.tags.clear()  # version 3

        self.post.rollback(2)

        self.assertEqual(list(self.post.tags.all()), [tag])

    def test_rollback_records_single_rollback_version(self):
        tag = Tag.objects.create(name="django")
        self.post.tags.add(tag)
        self.post.tags.clear()

        self.post.rollback(2)

        last = VersionControl.objects.latest_version(self.post)
        self.assertEqual((last.version, last.action), (4, "rollback"))
        self.assertEqual(self.post.current_version, 4)

    def test_rollback_records_user(self):
        user = get_user_model().objects.create_user("editor")
        self.post.title = "v2"
        self.post.save()

        self.post.rollback(1, user=user)

        self.assertEqual(VersionControl.objects.latest_version(self.post).user, user)

    def test_rollback_to_current_version_still_records_rollback(self):
        self.post.rollback(1)

        self.assertEqual(VersionControl.objects.latest_version(self.post).action, "rollback")

    def test_rollback_to_deleted_version_soft_deletes(self):
        self.post.delete()  # version 2 (tombstone)
        self.post.restore()  # version 3

        self.post.rollback(2)

        self.post.refresh_from_db()
        self.assertTrue(self.post.is_deleted)
        self.assertIsNotNone(self.post.deleted_at)

    def test_rollback_of_deleted_record_to_live_version_restores_it(self):
        self.post.delete()

        self.post.rollback(1)

        self.post.refresh_from_db()
        self.assertFalse(self.post.is_deleted)
        self.assertIsNone(self.post.deleted_at)

    def test_rollback_ignores_fields_missing_from_snapshot(self):
        VersionControl.objects.filter(pk=VersionControl.objects.get_version(self.post, 1).pk).update(
            data={"title": "old"}
        )

        self.post.rollback(1)

        self.post.refresh_from_db()
        self.assertEqual(self.post.title, "old")
        self.assertEqual(self.post.author, self.author)

    def test_rollback_to_missing_version_raises(self):
        with self.assertRaisesMessage(ValueError, "Version not found"):
            self.post.rollback(99)

    def test_rollback_does_not_touch_other_objects_history(self):
        other_post = Post.objects.create(title="other", author=self.author)
        self.post.title = "v2"
        self.post.save()

        self.post.rollback(1)

        self.assertEqual(VersionControl.objects.latest_version(other_post).version, 1)
