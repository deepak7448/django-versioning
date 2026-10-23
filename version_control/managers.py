from contextlib import contextmanager
from contextvars import ContextVar
from django.db import models, transaction
from django.contrib.contenttypes.models import ContentType
from typing import Optional, List

# When set, VersionedModelManager stops hiding soft-deleted rows (used during unique validation).
# A ContextVar keeps this per thread / per async task.
_include_deleted = ContextVar("version_control_include_deleted", default=False)


@contextmanager
def include_deleted():
    """
    Make VersionedModelManager querysets include soft-deleted rows inside this block.

    Example:
        >>> with include_deleted():
        ...     Post.objects.filter(title="dup").exists()  # also sees soft-deleted posts
    """
    token = _include_deleted.set(True)
    try:
        yield
    finally:
        _include_deleted.reset(token)


class VersionControlManager(models.Manager):
    """
    Manager for VersionControl to simplify querying versioned objects.

    Provides helpers to:
      - Fetch the latest version of an object.
      - Retrieve full version history.
      - Get a specific version by number.

    Methods:
        latest_version(obj):
            Return the latest version entry for an object.

        history(obj):
            Return all versions for an object in ascending order.

        get_version(obj, version_number):
            Return a specific version entry for an object.

    Example:
        >>> post = Post.objects.get(pk=1)
        >>> VersionControl.objects.latest_version(post)
        <VersionControl: Post v3 for 1>
    """

    def latest_version(self, obj: models.Model) -> Optional[models.Model]:
        """
        Return the latest version entry for an object.

        Args:
            obj: Instance of models.Model to retrieve the latest version for.

        Returns:
            Optional[models.Model]: The latest version entry for the given object, or None if no versions are found.
        """
        content_type = ContentType.objects.get_for_model(obj.__class__)
        return (
            self.filter(content_type=content_type, object_id=obj.pk)
            .order_by("-version")
            .first()
        )

    def history(self, obj: models.Model) -> List[models.Model]:
        """
        Return all versions for an object in ascending order.

        Args:
            obj: Instance of models.Model to retrieve the version history for.

        Returns:
            List[models.Model]: A list of all version entries for the given object in ascending order.
        """
        content_type = ContentType.objects.get_for_model(obj.__class__)
        return list(
            self.filter(content_type=content_type, object_id=obj.pk)
            .order_by("version")
        )

    def get_version(self, obj: models.Model, version_number: int) -> Optional[models.Model]:
        """
        Return the version entry for an object with the given version number.

        Args:
            obj: Instance of models.Model to retrieve the version for.
            version_number: The version number to retrieve.

        Returns:
            Optional[models.Model]: The version entry for the given object with the given version number, or None if no such version is found.
        """
        content_type = ContentType.objects.get_for_model(obj.__class__)
        return self.filter(
            content_type=content_type, object_id=obj.pk, version=version_number
        ).first()


class VersionedQuerySet(models.QuerySet):
    """
    QuerySet for VersionedModel whose bulk delete goes through the model's
    delete(), so soft deletion and tombstone versions apply to bulk operations
    (including the admin's "delete selected" action).
    """

    def delete(self, user: Optional[models.Model] = None):
        """
        Soft-delete every non-deleted object in the queryset, recording a delete version for each.

        Returns:
            tuple: (count, {model_label: count}), matching QuerySet.delete().
        """
        count = 0
        with transaction.atomic(using=self.db):
            for obj in self.filter(is_deleted=False):
                obj.delete(user=user)
                count += 1
        return count, {self.model._meta.label: count}

    # Keep from_queryset() from copying this onto the manager (Model.objects.delete() would wipe the table)
    delete.queryset_only = True

    def hard_delete(self, user: Optional[models.Model] = None):
        """
        Permanently delete every object in the queryset, recording a tombstone version for each.

        Returns:
            tuple: (count, {model_label: count}), matching QuerySet.delete().
        """
        count = 0
        with transaction.atomic(using=self.db):
            for obj in self:
                obj.delete(user=user, hard=True)
                count += 1
        return count, {self.model._meta.label: count}

    hard_delete.queryset_only = True


class VersionedModelManager(models.Manager.from_queryset(VersionedQuerySet)):
    """
    Manager for VersionedModel with built-in soft-delete handling.

    By default, `.all()` and `.get_queryset()` exclude deleted objects.

    Methods:
        get_queryset():
            Return only non-deleted records.

        all_with_deleted():
            Return all records, including soft-deleted ones.

        deleted():
            Return only soft-deleted records.

        restore(pk, user=None):
            Restore a soft-deleted object by primary key.

    Querysets also provide delete() (soft delete) and hard_delete(); see VersionedQuerySet.
    """

    def get_queryset(self) -> models.QuerySet:
        """
        Return only non-deleted records (all records inside an include_deleted() block).

        Example:
            >>> Post.objects.get_queryset()
            [<Post: Post v1 for 1>, <Post: Post v2 for 1>]
        """
        queryset = super().get_queryset()
        if _include_deleted.get():
            return queryset
        return queryset.filter(is_deleted=False)

    def all_with_deleted(self) -> models.QuerySet:
        """
        Return all records, including soft-deleted ones.

        Example:
            >>> Post.objects.all_with_deleted()
            [<Post: Post v1 for 1>, <Post: Post v2 for 1>, <Post: Post v3 for 1 (deleted)>]
        """
        return super().get_queryset()

    def deleted(self) -> models.QuerySet:
        """
        Return only soft-deleted records.

        Example:
            >>> Post.objects.deleted()
            [<Post: Post v3 for 1 (deleted)>]
        """
        return super().get_queryset().filter(is_deleted=True)

    def restore(self, pk: int, user: Optional[models.Model] = None) -> Optional[models.Model]:
        """
        Restore a soft-deleted object by primary key.

        Args:
            pk (int): Primary key of the object to restore.
            user (Optional[models.Model], optional): User who performed the restore action.

        Returns:
            Optional[models.Model]: Restored object if found, otherwise None.
        """
        obj = self.all_with_deleted().filter(pk=pk).first()
        if obj and obj.is_deleted:
            obj.restore(user=user)
        return obj