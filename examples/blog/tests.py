import json

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from io import StringIO

from blog.models import Article, Author
from version_control.models import VersionControl


class ApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("alice", password="pw")
        self.client.force_login(self.user)
        self.article = Article.objects.create(title="Hello", author=Author.objects.create(name="Ann"))

    def post_json(self, url, data=None):
        return self.client.post(url, json.dumps(data or {}), content_type="application/json")

    def test_update_records_request_user(self):
        response = self.post_json(f"/articles/{self.article.pk}/update/", {"title": "Hi"})

        self.assertEqual(response.json(), {"current_version": 2})
        self.assertEqual(VersionControl.objects.latest_version(self.article).user, self.user)

    def test_history(self):
        self.post_json(f"/articles/{self.article.pk}/update/", {"title": "Hi"})

        versions = self.client.get(f"/articles/{self.article.pk}/history/").json()["versions"]

        self.assertEqual([(v["version"], v["action"], v["user"]) for v in versions], [(1, "create", None), (2, "update", "alice")])

    def test_audit_and_diff(self):
        self.post_json(f"/articles/{self.article.pk}/update/", {"title": "Hi"})

        audit = self.client.get(f"/articles/{self.article.pk}/audit/").json()["audit_trail"]
        diff = self.client.get(f"/articles/{self.article.pk}/diff/1/2/").json()["diff"]

        self.assertEqual(audit[-1]["changed_by"], "alice")
        self.assertEqual(diff, {"title": {"from": "Hello", "to": "Hi"}})

    def test_diff_missing_version(self):
        self.assertEqual(self.client.get(f"/articles/{self.article.pk}/diff/1/9/").status_code, 404)

    def test_rollback(self):
        self.post_json(f"/articles/{self.article.pk}/update/", {"title": "Hi"})

        response = self.client.post(f"/articles/{self.article.pk}/rollback/1/")

        self.assertEqual(response.json()["title"], "Hello")

    def test_delete_and_restore(self):
        self.client.post(f"/articles/{self.article.pk}/delete/")
        self.assertFalse(Article.objects.filter(pk=self.article.pk).exists())

        self.client.post(f"/articles/{self.article.pk}/restore/")
        self.assertTrue(Article.objects.filter(pk=self.article.pk).exists())

    def test_hard_delete(self):
        self.client.post(f"/articles/{self.article.pk}/delete/?hard=1")

        self.assertFalse(Article.objects.all_with_deleted().filter(pk=self.article.pk).exists())

    def test_requires_login(self):
        self.client.logout()

        self.assertEqual(self.client.get(f"/articles/{self.article.pk}/history/").status_code, 302)


class AdminTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser("admin", "a@example.com", "pw")
        self.client.force_login(self.admin)
        self.article = Article.objects.create(title="Hello", author=Author.objects.create(name="Ann"))
        self.changelist = reverse("admin:blog_article_changelist")

    def test_changelist_shows_soft_deleted_rows_and_history_link(self):
        self.article.delete()

        response = self.client.get(self.changelist)

        self.assertContains(response, "Hello")
        self.assertContains(response, reverse("admin:version_control_versioncontrol_changelist"))

    def test_history_link_filters_version_admin(self):
        content_type = ContentType.objects.get_for_model(Article)
        url = reverse("admin:version_control_versioncontrol_changelist")

        response = self.client.get(url, {"content_type": content_type.pk, "object_id": self.article.pk})

        self.assertEqual(response.status_code, 200)
        self.assertEqual([v.object_id for v in response.context["cl"].result_list], [str(self.article.pk)])

    def test_delete_selected_soft_deletes(self):
        self.client.post(self.changelist, {"action": "delete_selected", "_selected_action": [self.article.pk], "post": "yes"})

        self.article.refresh_from_db()
        self.assertTrue(self.article.is_deleted)
        self.assertEqual(VersionControl.objects.latest_version(self.article).user, self.admin)

    def test_restore_action(self):
        self.article.delete()

        self.client.post(self.changelist, {"action": "restore_selected", "_selected_action": [self.article.pk]})

        self.article.refresh_from_db()
        self.assertFalse(self.article.is_deleted)

    def test_hard_delete_action(self):
        self.client.post(self.changelist, {"action": "hard_delete_selected", "_selected_action": [self.article.pk]})

        self.assertFalse(Article.objects.all_with_deleted().filter(pk=self.article.pk).exists())


class DemoCommandTests(TestCase):
    def test_demo_runs_and_rolls_back(self):
        out = StringIO()
        call_command("demo", stdout=out)

        self.assertIn("16. UUID primary keys", out.getvalue())
        self.assertFalse(Article.objects.all_with_deleted().exists())
