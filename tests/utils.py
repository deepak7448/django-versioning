from contextlib import contextmanager

from crequest.middleware import CrequestMiddleware
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

from tests.testapp.models import Author, Post


@contextmanager
def request_user(user):
    """Simulate CrequestMiddleware holding a request made by `user` (None = anonymous)."""
    request = RequestFactory().get("/")
    request.user = user or AnonymousUser()
    CrequestMiddleware.set_request(request)
    try:
        yield request
    finally:
        CrequestMiddleware.del_request()


def make_post(title="p", author=None, **kwargs):
    return Post.objects.create(title=title, author=author or Author.objects.create(name="Ann"), **kwargs)
