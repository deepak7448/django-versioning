import threading

from django.db import connection, connections
from django.test import TransactionTestCase, skipUnlessDBFeature

from version_control.models import VersionControl
from tests.testapp.models import Author, Post, Tag

THREADS = 8


def run_concurrently(target, n=THREADS):
    """Run target(i) in n threads released at the same moment; return the exceptions raised."""
    barrier = threading.Barrier(n)
    errors = []

    def worker(i):
        try:
            barrier.wait()
            target(i)
        except Exception as exc:  # collected and asserted on by the test
            errors.append(exc)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return errors


@skipUnlessDBFeature("has_select_for_update")
class ConcurrentVersioningTests(TransactionTestCase):
    """Real concurrent writers; needs a database with row locks (PostgreSQL, MySQL, Oracle), not SQLite."""

    def setUp(self):
        self.post = Post.objects.create(title="p", author=Author.objects.create(name="Ann"))

    def versions(self):
        return sorted(VersionControl.objects.filter(content_type__model="post", object_id=self.post.pk).values_list("version", flat=True))

    def test_concurrent_saves_get_distinct_versions(self):
        def save(i):
            post = Post.objects.get(pk=self.post.pk)
            post.title = f"title {i}"
            post.save()

        self.assertEqual(run_concurrently(save), [])
        self.assertEqual(self.versions(), list(range(1, THREADS + 2)))

    def test_concurrent_m2m_changes_get_distinct_versions(self):
        tags = [Tag.objects.create(name=f"t{i}") for i in range(THREADS)]

        def add(i):
            Post.objects.get(pk=self.post.pk).tags.add(tags[i])

        self.assertEqual(run_concurrently(add), [])
        self.assertEqual(self.versions(), list(range(1, THREADS + 2)))
        self.assertEqual(len(VersionControl.objects.latest_version(self.post).data["tags"]), THREADS)
