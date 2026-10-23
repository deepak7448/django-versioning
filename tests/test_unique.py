import datetime

from django.core.exceptions import ValidationError
from django.forms import modelform_factory
from django.test import TestCase

from version_control.managers import include_deleted
from tests.testapp.models import Author, Event, Post, Slugged

PostForm = modelform_factory(Post, fields=["title", "author"])


class UniqueValidationTests(TestCase):
    def setUp(self):
        self.author = Author.objects.create(name="Ann")

    def test_unique_field_check_sees_soft_deleted_rows(self):
        Post.objects.create(title="dup", author=self.author).delete()

        form = PostForm({"title": "dup", "author": self.author.pk})

        self.assertFalse(form.is_valid())
        self.assertIn("title", form.errors)

    def test_unique_constraint_check_sees_soft_deleted_rows(self):
        Slugged.objects.create(slug="s").delete()

        with self.assertRaises(ValidationError):
            Slugged(slug="s").full_clean()

    def test_editing_existing_object_is_not_a_conflict_with_itself(self):
        post = Post.objects.create(title="same", author=self.author)

        self.assertTrue(PostForm({"title": "same", "author": self.author.pk}, instance=post).is_valid())

    def test_unique_value_free_of_any_row_is_valid(self):
        self.assertTrue(PostForm({"title": "fresh", "author": self.author.pk}).is_valid())

    def test_default_manager_still_hides_soft_deleted_rows_after_validation(self):
        Post.objects.create(title="dup", author=self.author).delete()
        PostForm({"title": "dup", "author": self.author.pk}).is_valid()

        self.assertFalse(Post.objects.filter(title="dup").exists())


class IncludeDeletedTests(TestCase):
    def setUp(self):
        Post.objects.create(title="gone", author=Author.objects.create(name="Ann")).delete()

    def test_include_deleted_shows_soft_deleted_rows(self):
        with include_deleted():
            self.assertTrue(Post.objects.filter(title="gone").exists())

    def test_include_deleted_resets_after_exception(self):
        with self.assertRaises(RuntimeError):
            with include_deleted():
                raise RuntimeError

        self.assertFalse(Post.objects.filter(title="gone").exists())

    def test_nested_include_deleted(self):
        with include_deleted():
            with include_deleted():
                pass
            self.assertTrue(Post.objects.filter(title="gone").exists())
        self.assertFalse(Post.objects.filter(title="gone").exists())


class UniqueForDateTests(TestCase):
    def test_unique_for_date_check_sees_soft_deleted_rows(self):
        day = datetime.date(2026, 1, 1)
        Event.objects.create(title="launch", day=day).delete()

        with self.assertRaises(ValidationError):
            Event(title="launch", day=day).full_clean()
