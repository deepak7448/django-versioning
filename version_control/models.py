from django.db import models
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.serializers.json import DjangoJSONEncoder
from crequest.middleware import CrequestMiddleware
from django.utils import timezone
from django.conf import settings
from version_control.managers import VersionControlManager, VersionedModelManager
import hashlib, json

User = get_user_model()

class VersionControl(models.Model):
    """
    Stores snapshots of changes for versioned models, providing
    full audit history, rollback, and diff tracking.

    Features:
        - Tracks every change to versioned objects, including creation,
          updates, deletions, restorations, and rollbacks.
        - Stores full snapshot of object data along with a SHA-256 hash.
        - Precomputes human-readable diffs for easy audit and comparison.
        - Associates each version with the user who performed the action.

    Fields:
        content_type (ForeignKey to ContentType):
            The type of the related object being versioned.

        object_id (PositiveIntegerField):
            The primary key of the related object.

        version (PositiveIntegerField):
            The version number of this snapshot.

        data (JSONField):
            Full snapshot of the object’s state at this version.

        data_hash (CharField):
            SHA-256 hash of the snapshot data for integrity checks.

        action (CharField):
            Action performed: "create", "update", "delete", "restore", or "rollback".

        diff (JSONField, optional):
            Precomputed human-readable diff between this version and the previous one.

        created_at (DateTimeField):
            Timestamp when this version was created.

        user (ForeignKey to User, optional):
            User who performed the action. Null if no user context is available.

    Manager:
        objects (VersionControlManager):
            Custom manager to easily fetch latest version, history,
            or a specific version.

    Meta:
        unique_together = ("content_type", "object_id", "version")

    Example Usage:
        >>> post = Post.objects.get(pk=1)
        >>> VersionControl.objects.latest_version(post)
        <VersionControl: Post v3 for 1>
        >>> VersionControl.objects.history(post)
        [<VersionControl v1>, <VersionControl v2>, <VersionControl v3>]
        >>> VersionControl.objects.get_version(post, 2)
        <VersionControl: Post v2 for 1>
    """
    content_type = models.ForeignKey(ContentType,on_delete=models.SET_NULL,null=True,help_text="Content type of the application")
    object_id = models.PositiveIntegerField(null=True,help_text="Object ID of the application")
    version = models.PositiveIntegerField(help_text="Version number of the snapshot")
    data = models.JSONField(encoder=DjangoJSONEncoder, help_text="Full snapshot of the record")  # full snapshot
    data_hash = models.CharField(max_length=64, db_index=True, help_text="SHA-256 hash of the snapshot")  # SHA-256 hash of the snapshot
    action = models.CharField(max_length=20, help_text="Action performed: create, update, delete, restore, rollback")  # Action performed
    diff = models.JSONField(null=True, blank=True, encoder=DjangoJSONEncoder, help_text="Precomputed human-readable diff")  # precomputed human-readable diff
    created_at = models.DateTimeField(auto_now_add=True, help_text="Timestamp when the version was created")
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, help_text="User who updated the version")
    
    objects = VersionControlManager()

    class Meta:
        unique_together = ("content_type", "object_id", "version")

    def __str__(self):
        return f"{self.content_type} v{self.version} for {self.object_id}"


class VersionedModel(models.Model):
    """
    Abstract base model providing full versioning and soft-delete support.

    Features:
        - Tracks the current version of the record.
        - Supports soft deletion with timestamp and user tracking.
        - Integrates with VersionControl for version snapshots.
        - Works recursively with related VersionedModel objects (FKs and M2M).

    Fields:
        current_version (PositiveIntegerField):
            The current version number of this record. Automatically incremented on changes.

        is_deleted (BooleanField):
            Indicates whether the record has been soft-deleted. Defaults to False.

        deleted_at (DateTimeField):
            Timestamp indicating when the record was soft-deleted. Null if not deleted.

        deleted_by (ForeignKey to User):
            References the user who deleted the record. Null if deleted programmatically
            or if no user context is available.

    Manager:
        objects (VersionedModelManager):
            Custom manager that by default excludes soft-deleted objects,
            and provides helper methods for restoring and accessing deleted records.

    Usage:
        >>> post = Post.objects.create(title="Hello")
        >>> post.delete(user=request.user)
        >>> Post.objects.all()  # Excludes deleted records
        >>> Post.objects.deleted()  # Only soft-deleted records
        >>> Post.objects.restore(pk=post.pk, user=request.user)  # Restore soft-deleted record

    Notes:
        - This is an abstract model. Inherit from this class to enable versioning
          and soft-delete functionality for your models.
        - Version snapshots are automatically created whenever the record is saved,
          deleted, restored, or rolled back.
    """

    current_version = models.PositiveIntegerField(default=1, editable=False, help_text="Current version number of the record")
    is_deleted = models.BooleanField(default=False, editable=False, help_text="Indicates if the record is deleted")
    deleted_at = models.DateTimeField(null=True, blank=True, editable=False, help_text="Timestamp when the record was deleted")
    deleted_by = models.ForeignKey(
        User, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="deleted_%(class)s_set", editable=False, help_text="User who deleted the record"
    )

    objects = VersionedModelManager()

    class Meta:
        abstract = True

    # ----------------------------
    # Helpers
    # ----------------------------
    def _serialize_fields(self):
        """
        Serialize all fields of the model, excluding id, current_version, is_deleted, deleted_at, and deleted_by.

        ForeignKey and FileField values are serialized as their pk and name, respectively.
        ManyToManyField values are serialized as a list of their related objects' ids.

        Returns:
            dict: A dictionary containing serialized values of all fields.
        """
        data = {}
        skip_fields = {"id", "current_version", "is_deleted", "deleted_at", "deleted_by"}

        for field in self._meta.fields:
            if field.name in skip_fields:
                continue
            value = getattr(self, field.name)
            if isinstance(field, models.ForeignKey):
                data[field.name] = value.pk if value else None
            elif isinstance(field, models.FileField):
                data[field.name] = value.name if value else None
            else:
                data[field.name] = value

        for field in self._meta.many_to_many:
            data[field.name] = list(getattr(self, field.name).values_list("id", flat=True))

        return data

    @staticmethod
    def _compute_hash(data: dict) -> str:
        """
        Compute the SHA-256 hash of a given dictionary.

        Args:
            data (dict): Dictionary to compute the hash for.

        Returns:
            str: The computed hash as a hexadecimal string.
        """
        normalized = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def _get_last_version(self):
        """
        Return the latest version entry for this object, or None if no versions are found.

        Args:
            None

        Returns:
            Optional[VersionControl]: The latest version entry for this object, or None if no versions are found.
        """
        content_type = ContentType.objects.get_for_model(self.__class__)
        return VersionControl.objects.filter(content_type=content_type, object_id=self.pk).order_by("-version").first()

    # ----------------------------
    # Versioning
    # ----------------------------
    def save_version(self, user=None, tombstone=False, action="update"):
        # Save related FK/M2M objects
        """
        Save a version snapshot of this object, including related FK/M2M objects.

        Args:
            user: Optional[User]: The user who triggered the save. If None, will attempt to resolve from the active request.
            tombstone: bool: If True, will save a "tombstone" version with a deleted marker.
            action: str: The action to store in the version history (create, update, delete, restore, rollback).

        Returns:
            None
        """
        for field in self._meta.fields:
            if isinstance(field, models.ForeignKey):
                obj = getattr(self, field.name)
                if isinstance(obj, VersionedModel):
                    obj.save_version(user=user)
        for field in self._meta.many_to_many:
            for obj in getattr(self, field.name).all():
                if isinstance(obj, VersionedModel):
                    obj.save_version(user=user)

        last = self._get_last_version()
        action = "create" if not last else action

        if tombstone:
            snapshot = {"__deleted__": True}
            action = "delete"
            data_hash = self._compute_hash(snapshot)
        else:
            snapshot = self._serialize_fields()
            if self.is_deleted:
                snapshot["__deleted__"] = True
            data_hash = self._compute_hash(snapshot)
            if last and last.data_hash == data_hash and action not in ("delete", "restore", "rollback"):
                return

        # Compute human-readable diff
        last_data = last.data if last else {}
        diff = {
            k: {"old": last_data.get(k), "new": v}
            for k, v in snapshot.items() if last_data.get(k) != v
        }

        # Save VersionControl entry
        version_number = (last.version + 1) if last else 1
        content_type = ContentType.objects.get_for_model(self.__class__)
        VersionControl.objects.create(
            content_type=content_type,
            object_id=self.pk,
            version=version_number,
            data=snapshot,
            data_hash=data_hash,
            action=action,
            diff=None,
            user=user or self._get_request_user()
        )

        # Update current_version
        self.current_version = version_number
        super().save(update_fields=["current_version"])

    @staticmethod
    def _get_request_user():
        """
        Returns the current user if the request is authenticated and a user is available, otherwise None.

        :return: Optional[User]
        """
        request = CrequestMiddleware.get_request()
        if request and hasattr(request, "user") and request.user.is_authenticated:
            return request.user
        return None

    # ----------------------------
    # Lifecycle methods
    # ----------------------------
    def save(self, *args, user=None, **kwargs):
        """
        Save a record and automatically create a version control snapshot with action "create" or "update".

        Args:
            user (User, optional): User who initiated the save. Defaults to None.

        """
        super().save(*args, **kwargs)
        self.save_version(user=user)

    def delete(self, user=None, hard=False, *args, **kwargs):
        """
        Delete a record.

        If `hard=True`, the record is deleted without leaving a tombstone.
        Otherwise, the record is marked as deleted and a version control snapshot is saved with action "delete".

        :param user: User who initiated the delete
        :param hard: If True, delete the record without leaving a tombstone (default: False)
        :return: None
        """
        if hard:
            self.current_version += 1
            self.save_version(user=user, tombstone=True)
            return super().delete(*args, **kwargs)

        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.deleted_by = user
        self.current_version += 1
        super().save(*args, **kwargs)
        self.save_version(user=user, tombstone=True, action="delete")

    def restore(self, user=None, *args, **kwargs):
        """
        Restore a deleted record.

        If the record is not deleted, do nothing.

        After restoring, save the record with an incremented version number
        and save a version control snapshot with action "restore".

        :param user: User who initiated the restore
        :return: None
        """
        if not self.is_deleted:
            return
        self.is_deleted = False
        self.deleted_at = None
        self.deleted_by = None
        self.current_version += 1
        super().save(*args, **kwargs)
        self.save_version(user=user, action="restore")

    # ----------------------------
    # Rollback
    # ----------------------------
    def rollback(self, version_number, user=None, *args, **kwargs):
        """
        Rollback a record to a specific version.

        :param version_number: int version to rollback to
        :param user: User who initiated the rollback
        :raises ValueError: if the version is not found
        """
        snapshot = VersionControl.objects.filter(
            content_type=ContentType.objects.get_for_model(self.__class__),
            object_id=self.pk,
            version=version_number
        ).first()
        if not snapshot:
            raise ValueError("Version not found")

        data = snapshot.data
        if data.get("__deleted__"):
            self.is_deleted = True
            self.deleted_at = timezone.now()
        else:
            for field in self._meta.fields:
                if field.name not in {"id", "current_version", "is_deleted", "deleted_at", "deleted_by"}:
                    setattr(self, field.name, data.get(field.name))
        self.is_deleted = False
        self.deleted_at = None
        self.deleted_by = None

        self.current_version = version_number
        super().save(*args, **kwargs)
        self.save_version(user=user, action="rollback")

    # ----------------------------
    # Diff between versions
    # ----------------------------
    def diff_versions(self, v1, v2):
        """
        Compute the diff between two versions of self.

        Args:
            v1 (int): The version number of the first snapshot.
            v2 (int): The version number of the second snapshot.

        Returns:
            dict: A dictionary with the following format:
                {
                    "field_name": {
                        "from": old_val,
                        "to": new_val
                    }
                }

        Raises:
            ValueError: If either version is not found for self.
        """
        content_type = ContentType.objects.get_for_model(self.__class__)
        
        snap1 = VersionControl.objects.filter(
            content_type=content_type,
            object_id=self.pk,
            version=v1
        ).first()
        
        snap2 = VersionControl.objects.filter(
            content_type=content_type,
            object_id=self.pk,
            version=v2
        ).first()
        
        if not snap1 or not snap2:
            missing_versions = []
            if not snap1:
                missing_versions.append(v1)
            if not snap2:
                missing_versions.append(v2)
            raise ValueError(
                f"Version(s) {missing_versions} not found for "
                f"{self.__class__.__name__} with ID {self.pk}"
            )
        
        changes = {}
        for key in snap1.data.keys() | snap2.data.keys():
            old_val, new_val = snap1.data.get(key), snap2.data.get(key)
            if old_val != new_val:
                if isinstance(old_val, list) and isinstance(new_val, list):
                    added = list(set(new_val) - set(old_val))
                    removed = list(set(old_val) - set(new_val))
                    if added or removed:
                        changes[key] = {"added": added, "removed": removed}
                else:
                    changes[key] = {"from": old_val, "to": new_val}
        
        return changes


    # ----------------------------
    # Audit trail
    # ----------------------------
    def audit_trail(self):
        """
        Generate a human-readable audit trail for this object.

        Returns a list of dictionaries, each representing a version of the object.
        Each dictionary contains the following keys:
            - version (int): The version number of the snapshot.
            - changed_by (str): The username of the user who performed the action.
            - changed_at (datetime): The timestamp when the version was created.
            - changes (dict): A dictionary containing the changed fields and their old and new values.
        """
        versions = VersionControl.objects.filter(
            content_type=ContentType.objects.get_for_model(self.__class__),
            object_id=self.pk
        ).order_by("version").values("version", "user__username", "created_at", "data")

        report = []
        previous_data = None
        for v in versions:
            if previous_data:
                changes = {}
                for key in previous_data.keys() | v["data"].keys():
                    old_val, new_val = previous_data.get(key), v["data"].get(key)
                    if old_val != new_val:
                        if isinstance(old_val, list) and isinstance(new_val, list):
                            added = list(set(new_val) - set(old_val))
                            removed = list(set(old_val) - set(new_val))
                            if added or removed:
                                changes[key] = {"added": added, "removed": removed}
                        else:
                            changes[key] = {"from": old_val, "to": new_val}
                if changes:
                    report.append({
                        "version": v["version"],
                        "changed_by": v["user__username"],
                        "changed_at": v["created_at"],
                        "changes": changes
                    })
            previous_data = v["data"]

        return report
