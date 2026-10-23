import uuid

from django.db import models

from version_control.models import VersionedModel


class Author(VersionedModel):
    name = models.CharField(max_length=100)

    def __str__(self):
        return self.name


class Tag(VersionedModel):
    name = models.CharField(max_length=50, unique=True)

    def __str__(self):
        return self.name


class Article(VersionedModel):
    title = models.CharField(max_length=200, unique=True)
    body = models.TextField(blank=True)
    author = models.ForeignKey(Author, on_delete=models.CASCADE, related_name="articles")
    tags = models.ManyToManyField(Tag, blank=True, related_name="articles")
    published_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.title


class Page(VersionedModel):
    """UUID primary keys are versioned the same way as integer ones."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slug = models.SlugField(unique=True)
    content = models.TextField(blank=True)

    def __str__(self):
        return self.slug
