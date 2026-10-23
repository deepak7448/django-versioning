from django.contrib.admin.sites import site
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from version_control.admin import VersionControlAdmin
from version_control.models import VersionControl
from tests.testapp.models import Author, Document, Post


class VersionControlAdminTests(TestCase):
    def setUp(self):
        self.admin = VersionControlAdmin(VersionControl, site)
        self.user = get_user_model().objects.create_superuser("admin", "a@example.com", "pw")
        self.client.force_login(self.user)
        self.author = Author.objects.create(name="Ann")
        self.changelist_url = reverse("admin:version_control_versioncontrol_changelist")

    def resource(self, obj):
        return str(self.admin.resource(VersionControl.objects.latest_version(obj)))

    # Resource column

    def test_resource_links_to_registered_model(self):
        post = Post.objects.create(title="p", author=self.author)

        result = self.resource(post)

        self.assertIn(reverse("admin:testapp_post_change", args=[post.pk]), result)
        self.assertIn("Testapp | Post - Post object", result)

    def test_resource_for_model_not_registered_in_admin(self):
        result = self.resource(self.author)

        self.assertIn(f"Author object ({self.author.pk})", result)
        self.assertNotIn("<a", result)

    def test_resource_for_stale_content_type(self):
        stale = ContentType.objects.create(app_label="gone", model="removed")
        version = VersionControl.objects.create(
            content_type=stale, object_id="1", version=1, data={}, data_hash="x", action="create"
        )

        self.assertIn("ID: 1", str(self.admin.resource(version)))

    def test_resource_marks_soft_deleted_objects(self):
        post = Post.objects.create(title="p", author=self.author)
        post.delete()

        result = self.resource(post)

        self.assertIn("(deleted)", result)
        self.assertNotIn("<a", result)

    def test_resource_for_hard_deleted_object(self):
        post = Post.objects.create(title="p", author=self.author)
        pk = post.pk
        post.delete(hard=True)
        version = VersionControl.objects.filter(content_type__model="post", object_id=pk).last()

        self.assertIn(f"Deleted (ID: {pk})", str(self.admin.resource(version)))

    def test_resource_for_uuid_object(self):
        doc = Document.objects.create(name="d")

        self.assertIn("Document object", self.resource(doc))

    def test_resource_without_content_type(self):
        version = VersionControl(version=1, data={}, data_hash="x", action="create")

        self.assertEqual(self.admin.resource(version), "—")

    # User column

    def test_linked_user_links_to_user_admin(self):
        post = Post.objects.create(title="p", author=self.author)
        post.title = "q"
        post.save(user=self.user)

        result = str(self.admin.linked_user(VersionControl.objects.latest_version(post)))

        self.assertIn(reverse("admin:auth_user_change", args=[self.user.pk]), result)

    def test_linked_user_without_user(self):
        self.assertEqual(self.admin.linked_user(VersionControl.objects.latest_version(self.author)), "—")

    # Views and permissions

    def test_changelist_renders(self):
        Post.objects.create(title="p", author=self.author).delete()

        response = self.client.get(self.changelist_url)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="action"')  # no bulk actions

    def test_change_view_is_read_only(self):
        version = VersionControl.objects.latest_version(self.author)

        response = self.client.get(reverse("admin:version_control_versioncontrol_change", args=[version.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="_save"')

    def test_add_view_is_forbidden(self):
        self.assertEqual(self.client.get(reverse("admin:version_control_versioncontrol_add")).status_code, 403)

    def test_delete_view_is_forbidden(self):
        version = VersionControl.objects.latest_version(self.author)

        response = self.client.get(reverse("admin:version_control_versioncontrol_delete", args=[version.pk]))

        self.assertEqual(response.status_code, 403)

    def test_post_to_change_view_does_not_modify(self):
        version = VersionControl.objects.latest_version(self.author)

        self.client.post(
            reverse("admin:version_control_versioncontrol_change", args=[version.pk]), {"action": "tampered"}
        )

        version.refresh_from_db()
        self.assertEqual(version.action, "create")

    # Filters and search

    def test_action_filter(self):
        Post.objects.create(title="p", author=self.author).delete()

        response = self.client.get(self.changelist_url, {"action": "delete"})

        self.assertEqual([v.action for v in response.context["cl"].result_list], ["delete"])

    def test_date_range_filter(self):
        response = self.client.get(
            self.changelist_url, {"created_at__range__gte": "2000-01-01", "created_at__range__lte": "2000-01-02"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["cl"].result_list), 0)

    def test_search_by_object_id(self):
        post = Post.objects.create(title="p", author=self.author)

        response = self.client.get(self.changelist_url, {"q": str(post.pk)})

        self.assertIn(post.pk, [int(v.object_id) for v in response.context["cl"].result_list])

    def test_search_by_username(self):
        post = Post.objects.create(title="p", author=self.author)
        post.title = "q"
        post.save(user=self.user)

        response = self.client.get(self.changelist_url, {"q": "admin"})

        self.assertEqual([v.user for v in response.context["cl"].result_list], [self.user])

    # Performance

    def test_changelist_query_count_is_constant(self):
        self.client.get(self.changelist_url)  # warm caches (content types, session)

        def count():
            with CaptureQueriesContext(connection) as ctx:
                self.assertEqual(self.client.get(self.changelist_url).status_code, 200)
            return len(ctx)

        Post.objects.create(title="a", author=self.author)
        few = count()
        for i in range(5):
            Post.objects.create(title=f"b{i}", author=self.author).save(user=self.user)
        self.assertEqual(count(), few)
