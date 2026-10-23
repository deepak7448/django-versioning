from django.contrib.auth import get_user_model
from django.test import TestCase

from version_control.models import VersionControl
from tests.testapp.models import Person, Tag
from tests.utils import make_post, request_user


class ManyToManyTests(TestCase):
    def setUp(self):
        self.post = make_post()
        self.tag = Tag.objects.create(name="a")
        self.tag2 = Tag.objects.create(name="b")

    def latest(self, obj):
        return VersionControl.objects.latest_version(obj)

    def test_add_records_version(self):
        self.post.tags.add(self.tag)

        self.assertEqual(self.latest(self.post).data["tags"], [self.tag.pk])
        self.assertEqual(self.latest(self.post).action, "update")

    def test_remove_records_version(self):
        self.post.tags.add(self.tag, self.tag2)
        self.post.tags.remove(self.tag)

        self.assertEqual(self.latest(self.post).data["tags"], [self.tag2.pk])

    def test_clear_records_version(self):
        self.post.tags.add(self.tag)
        self.post.tags.clear()

        self.assertEqual(self.latest(self.post).data["tags"], [])

    def test_m2m_change_records_request_user(self):
        user = get_user_model().objects.create_user("editor")
        with request_user(user):
            self.post.tags.add(self.tag)

        self.assertEqual(self.latest(self.post).user, user)

    def test_reverse_add_versions_forward_object(self):
        self.tag.post_set.add(self.post)

        self.assertEqual(self.latest(self.post).data["tags"], [self.tag.pk])

    def test_reverse_remove_versions_forward_object(self):
        self.post.tags.add(self.tag)
        self.tag.post_set.remove(self.post)

        self.assertEqual(self.latest(self.post).data["tags"], [])

    def test_reverse_clear_versions_forward_object(self):
        self.post.tags.add(self.tag)
        self.tag.post_set.clear()

        self.assertEqual(self.latest(self.post).data["tags"], [])


class SymmetricalManyToManyTests(TestCase):
    def test_symmetrical_self_m2m_does_not_recurse_forever(self):
        x = Person.objects.create(name="x")
        y = Person.objects.create(name="y")
        x.friends.add(y)

        x.name = "x2"
        x.save()

        self.assertEqual(VersionControl.objects.latest_version(x).data["name"], "x2")

    def test_symmetrical_add_versions_both_sides(self):
        x = Person.objects.create(name="x")
        y = Person.objects.create(name="y")

        with self.captureOnCommitCallbacks(execute=True):
            x.friends.add(y)

        self.assertEqual(VersionControl.objects.latest_version(x).data["friends"], [y.pk])
        self.assertEqual(VersionControl.objects.latest_version(y).data["friends"], [x.pk])

    def test_symmetrical_remove_versions_both_sides(self):
        x = Person.objects.create(name="x")
        y = Person.objects.create(name="y")
        with self.captureOnCommitCallbacks(execute=True):
            x.friends.add(y)

        x.friends.remove(y)

        self.assertEqual(VersionControl.objects.latest_version(y).data["friends"], [])

    def test_symmetrical_clear_versions_both_sides(self):
        x = Person.objects.create(name="x")
        y = Person.objects.create(name="y")
        with self.captureOnCommitCallbacks(execute=True):
            x.friends.add(y)

        x.friends.clear()

        self.assertEqual(VersionControl.objects.latest_version(y).data["friends"], [])

    def test_rollback_of_symmetrical_relation_versions_peer(self):
        x = Person.objects.create(name="x")
        y = Person.objects.create(name="y")
        with self.captureOnCommitCallbacks(execute=True):
            x.friends.add(y)  # x v2
        x.friends.clear()  # x v3

        with self.captureOnCommitCallbacks(execute=True):
            x.rollback(2)

        self.assertEqual(VersionControl.objects.latest_version(y).data["friends"], [x.pk])
