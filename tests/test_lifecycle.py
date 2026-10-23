from unittest import mock

from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.db.utils import IntegrityError
from django.test import TestCase

from version_control.models import VersionControl
from tests.testapp.models import Author, Document, Post
from tests.utils import make_post, request_user


class CreateTests(TestCase):
    def test_create_records_version_one(self):
        post = make_post()

        version = VersionControl.objects.latest_version(post)
        self.assertEqual((version.version, version.action), (1, "create"))
        self.assertEqual(post.current_version, 1)

    def test_snapshot_contents(self):
        author = Author.objects.create(name="Ann")
        post = Post.objects.create(title="p", author=author, attachment="attachments/a.txt")

        data = VersionControl.objects.latest_version(post).data

        self.assertEqual(data["title"], "p")
        self.assertEqual(data["author"], author.pk)
        self.assertEqual(data["attachment"], "attachments/a.txt")
        self.assertEqual(data["tags"], [])
        self.assertIsInstance(data["published_at"], str)

    def test_snapshot_excludes_bookkeeping_fields(self):
        data = VersionControl.objects.latest_version(make_post()).data

        for field in ("id", "current_version", "is_deleted", "deleted_at", "deleted_by"):
            self.assertNotIn(field, data)

    def test_create_diff_lists_every_field(self):
        diff = VersionControl.objects.latest_version(make_post(title="p")).diff

        self.assertEqual(diff["title"], {"old": None, "new": "p"})

    def test_snapshot_hash_is_sha256_hex(self):
        version = VersionControl.objects.latest_version(make_post())

        self.assertEqual(len(version.data_hash), 64)
        int(version.data_hash, 16)

    def test_str(self):
        post = make_post()

        version = VersionControl.objects.latest_version(post)

        self.assertEqual(str(version), f"{version.content_type} v1 for {post.pk}")


class UpdateTests(TestCase):
    def setUp(self):
        self.post = make_post(title="v1")

    def test_update_records_new_version(self):
        self.post.title = "v2"
        self.post.save()

        version = VersionControl.objects.latest_version(self.post)
        self.assertEqual((version.version, version.action), (2, "update"))

    def test_update_bumps_current_version_in_memory_and_database(self):
        self.post.title = "v2"
        self.post.save()

        self.assertEqual(self.post.current_version, 2)
        self.assertEqual(Post.objects.get(pk=self.post.pk).current_version, 2)

    def test_version_stores_diff_of_changed_fields(self):
        self.post.title = "v2"
        self.post.save()

        self.assertEqual(VersionControl.objects.latest_version(self.post).diff, {"title": {"old": "v1", "new": "v2"}})

    def test_unchanged_save_records_no_version(self):
        self.post.save()
        Post.objects.get(pk=self.post.pk).save()

        self.assertEqual(VersionControl.objects.filter(object_id=self.post.pk, content_type__model="post").count(), 1)

    def test_save_with_update_fields(self):
        self.post.title = "v2"
        self.post.save(update_fields=["title"])

        self.assertEqual(VersionControl.objects.latest_version(self.post).data["title"], "v2")

    def test_save_sends_a_single_post_save_signal(self):
        received = []
        handler = lambda **kwargs: received.append(kwargs)
        post_save.connect(handler, sender=Post)
        try:
            self.post.title = "v2"
            self.post.save()
        finally:
            post_save.disconnect(handler, sender=Post)

        self.assertEqual(len(received), 1)


class UserAttributionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("editor")
        self.other = get_user_model().objects.create_user("other")

    def test_explicit_user(self):
        post = make_post()
        post.title = "v2"
        post.save(user=self.user)

        self.assertEqual(VersionControl.objects.latest_version(post).user, self.user)

    def test_request_user_used_when_none_passed(self):
        with request_user(self.user):
            post = make_post()

        self.assertEqual(VersionControl.objects.latest_version(post).user, self.user)

    def test_explicit_user_wins_over_request_user(self):
        post = make_post()
        with request_user(self.other):
            post.title = "v2"
            post.save(user=self.user)

        self.assertEqual(VersionControl.objects.latest_version(post).user, self.user)

    def test_anonymous_request_records_no_user(self):
        with request_user(None):
            post = make_post()

        self.assertIsNone(VersionControl.objects.latest_version(post).user)

    def test_no_request_records_no_user(self):
        self.assertIsNone(VersionControl.objects.latest_version(make_post()).user)


class RelatedObjectVersioningTests(TestCase):
    def test_cached_related_object_without_history_is_versioned(self):
        author = Author.objects.create(name="Ann")
        VersionControl.objects.all().delete()  # simulate data that predates versioning

        Post.objects.create(title="p", author=author)

        self.assertIsNotNone(VersionControl.objects.latest_version(author))

    def test_unloaded_related_object_is_not_fetched(self):
        author = Author.objects.create(name="Ann")
        post = Post.objects.get(pk=make_post(author=author).pk)
        post.title = "v2"
        post.save()

        self.assertFalse(Post.author.is_cached(post))

    def test_unchanged_related_object_gets_no_new_version(self):
        author = Author.objects.create(name="Ann")
        make_post(author=author)

        self.assertEqual(VersionControl.objects.latest_version(author).version, 1)


class AtomicityTests(TestCase):
    def test_model_save_rolls_back_when_version_cannot_be_written(self):
        author = Author.objects.create(name="Ann")
        with mock.patch.object(VersionControl.objects, "create", side_effect=IntegrityError("boom")):
            with self.assertRaises(IntegrityError):
                Post.objects.create(title="p", author=author)

        self.assertFalse(Post.objects.all_with_deleted().filter(title="p").exists())

    def test_soft_delete_rolls_back_when_version_cannot_be_written(self):
        post = make_post()
        with mock.patch.object(VersionControl.objects, "create", side_effect=IntegrityError("boom")):
            with self.assertRaises(IntegrityError):
                post.delete()

        self.assertFalse(Post.objects.all_with_deleted().get(pk=post.pk).is_deleted)


class NonIntegerPrimaryKeyTests(TestCase):
    def test_uuid_primary_key_is_versioned(self):
        doc = Document.objects.create(name="a")
        doc.name = "b"
        doc.save()

        self.assertEqual([v.data["name"] for v in VersionControl.objects.history(doc)], ["a", "b"])

    def test_uuid_rollback(self):
        doc = Document.objects.create(name="a")
        doc.name = "b"
        doc.save()

        doc.rollback(1)

        self.assertEqual(Document.objects.get(pk=doc.pk).name, "a")

    def test_uuid_primary_key_not_in_snapshot(self):
        doc = Document.objects.create(name="a")

        self.assertEqual(VersionControl.objects.latest_version(doc).data, {"name": "a"})
