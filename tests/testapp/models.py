import uuid

from django.db import models
from django.utils import timezone
from version_control.models import VersionedModel


class Author(VersionedModel):
    name = models.CharField(max_length=50)


class Tag(VersionedModel):
    name = models.CharField(max_length=50)


class Post(VersionedModel):
    title = models.CharField(max_length=50, unique=True)
    author = models.ForeignKey(Author, on_delete=models.CASCADE)
    tags = models.ManyToManyField(Tag, blank=True)
    published_at = models.DateTimeField(default=timezone.now)
    attachment = models.FileField(upload_to="attachments/", blank=True)


class Person(VersionedModel):
    name = models.CharField(max_length=50)
    friends = models.ManyToManyField("self", blank=True)


class Document(VersionedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=50)


class Slugged(VersionedModel):
    slug = models.CharField(max_length=50)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["slug"], name="unique_slug")]


class Event(VersionedModel):
    title = models.CharField(max_length=50, unique_for_date="day")
    day = models.DateField()
