from django.db import models, router, transaction
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.serializers.json import DjangoJSONEncoder
from crequest.middleware import CrequestMiddleware
from django.utils import timezone
from django.conf import settings
from version_control.managers import VersionControlManager, VersionedModelManager, include_deleted
import datetime, hashlib, json

# Bookkeeping fields of VersionedModel that are never snapshotted or rolled back
UNVERSIONED_FIELDS = {"id", "current_version", "is_deleted", "deleted_at", "deleted_by"}


class SnapshotEncoder(DjangoJSONEncoder):
    """
    DjangoJSONEncoder that keeps microseconds. DjangoJSONEncoder truncates
    datetimes and times to milliseconds, so rollback would lose precision and
    sub-millisecond changes would hash the same.
    """

    def default(self, o):
        if isinstance(o, datetime.datetime) and o.microsecond:
            r = o.isoformat()
            return r[:-6] + "Z" if r.endswith("+00:00") else r
        if isinstance(o, datetime.time) and o.microsecond and not timezone.is_aware(o):
            return o.isoformat()
        return super().default(o)


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

        object_id (CharField):
            The primary key of the related object, stored as text so integer,
            UUID and string primary keys are all supported.

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
    object_id = models.CharField(max_length=255, null=True, help_text="Object ID of the application")
    version = models.PositiveIntegerField(help_text="Version number of the snapshot")
    data = models.JSONField(encoder=DjangoJSONEncoder, help_text="Full snapshot of the record")  # full snapshot
    data_hash = models.CharField(max_length=64, db_index=True, help_text="SHA-256 hash of the snapshot")  # SHA-256 hash of the snapshot
    action = models.CharField(max_length=20, help_text="Action performed: create, update, delete, restore, rollback")  # Action performed
    diff = models.JSONField(null=True, blank=True, encoder=DjangoJSONEncoder, help_text="Precomputed human-readable diff")  # precomputed human-readable diff
    created_at = models.DateTimeField(auto_now_add=True, help_text="Timestamp when the version was created")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, help_text="User who updated the version")

    # Not a database column; lets callers prefetch_related("content_object") the versioned objects
    content_object = GenericForeignKey("content_type", "object_id")

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
        - Works recursively with related VersionedModel objects (FKs and M2M)
          that are already loaded in memory.

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
          deleted, restored, or rolled back. Each operation and its snapshot are
          written in a single transaction.
        - QuerySet.update() and cascade deletes bypass versioning.
    """

    current_version = models.PositiveIntegerField(default=1, editable=False, help_text="Current version number of the record")
    is_deleted = models.BooleanField(default=False, editable=False, help_text="Indicates if the record is deleted")
    deleted_at = models.DateTimeField(null=True, blank=True, editable=False, help_text="Timestamp when the record was deleted")
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
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
        Serialize all fields of the model, excluding the primary key, current_version, is_deleted, deleted_at, and deleted_by.

        ForeignKey and FileField values are serialized as their pk and name, respectively.
        ManyToManyField values are serialized as a list of their related objects' pks.

        Returns:
            dict: A dictionary containing serialized values of all fields.
        """
        data = {}

        for field in self._meta.fields:
            if field.primary_key or field.name in UNVERSIONED_FIELDS:
                continue
            if isinstance(field, models.ForeignKey):
                # attname reads the raw pk without fetching the related object
                data[field.name] = getattr(self, field.attname)
            elif isinstance(field, models.FileField):
                value = getattr(self, field.name)
                data[field.name] = value.name if value else None
            else:
                data[field.name] = getattr(self, field.name)

        for field in self._meta.many_to_many:
            data[field.name] = list(getattr(self, field.name).values_list("pk", flat=True))

        # Round-trip through the encoder so the snapshot matches what is stored in the JSONField
        # (dates, decimals, UUIDs become strings); otherwise diffs against stored data never match.
        return json.loads(json.dumps(data, cls=SnapshotEncoder))

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

    @staticmethod
    def _diff_snapshots(old: dict, new: dict) -> dict:
        """
        Compare two snapshots field by field.

        Returns:
            dict: {"field": {"from": old, "to": new}} for changed values, and
            {"field": {"added": [...], "removed": [...]}} for changed lists (M2M pks).
        """
        changes = {}
        for key in old.keys() | new.keys():
            old_val, new_val = old.get(key), new.get(key)
            if old_val == new_val:
                continue
            if isinstance(old_val, list) and isinstance(new_val, list):
                added = [v for v in new_val if v not in old_val]
                removed = [v for v in old_val if v not in new_val]
                if added or removed:
                    changes[key] = {"added": added, "removed": removed}
            else:
                changes[key] = {"from": old_val, "to": new_val}
        return changes

    def _db_alias(self, using=None):
        return using or router.db_for_write(self.__class__, instance=self)

    @classmethod
    def _lock_rows(cls, pks, using=None):
        """
        SELECT ... FOR UPDATE the given rows, in primary key order so concurrent
        lockers can't deadlock each other. Must run inside a transaction.
        """
        queryset = cls._base_manager.select_for_update().filter(pk__in=list(pks)).order_by("pk")
        if using:
            queryset = queryset.using(using)
        list(queryset.values_list("pk", flat=True))

    def _get_last_version(self):
        """
        Return the latest version entry for this object, or None if no versions are found.

        Args:
            None

        Returns:
            Optional[VersionControl]: The latest version entry for this object, or None if no versions are found.
        """
        return VersionControl.objects.latest_version(self)

    # ----------------------------
    # Unique validation
    # ----------------------------
    # The default manager hides soft-deleted rows, but the database still enforces
    # uniqueness against them. Include them while Django validates uniqueness so
    # forms report the conflict instead of failing with an IntegrityError on save.
    def _perform_unique_checks(self, unique_checks):
        with include_deleted():
            return super()._perform_unique_checks(unique_checks)

    def _perform_date_checks(self, date_checks):
        with include_deleted():
            return super()._perform_date_checks(date_checks)

    def validate_constraints(self, exclude=None):
        with include_deleted():
            return super().validate_constraints(exclude=exclude)

    # ----------------------------
    # Versioning
    # ----------------------------
    def save_version(self, user=None, tombstone=False, action="update", _visited=None, _locked=False):
        """
        Save a version snapshot of this object, including related FK/M2M objects.

        Related objects are only versioned when they are already loaded in memory
        (a cached FK or a prefetched M2M); objects that would have to be fetched
        from the database cannot carry unsaved changes, so they are skipped.

        Args:
            user: Optional[User]: The user who triggered the save. If None, will attempt to resolve from the active request.
            tombstone: bool: If True, will save a "tombstone" version with a deleted marker.
            action: str: The action to store in the version history (create, update, delete, restore, rollback).
            _visited: Internal set of (model, pk) already versioned in this pass, so cyclic
                relations (e.g. symmetrical self M2M) terminate.
            _locked: Internal; True when the caller already wrote this row in the current
                transaction, which holds its row lock, so no extra SELECT ... FOR UPDATE is needed.

        Returns:
            None
        """
        if _visited is None:
            _visited = set()
        key = (self.__class__, self.pk)
        if key in _visited:
            return
        _visited.add(key)

        # Save related FK/M2M objects
        for field in self._meta.fields:
            if isinstance(field, models.ForeignKey) and field.is_cached(self):
                obj = getattr(self, field.name)
                if isinstance(obj, VersionedModel):
                    obj.save_version(user=user, _visited=_visited)
        prefetched = getattr(self, "_prefetched_objects_cache", {})
        for field in self._meta.many_to_many:
            for obj in prefetched.get(field.name, ()):
                if isinstance(obj, VersionedModel):
                    obj.save_version(user=user, _visited=_visited)

        if tombstone:
            action = "delete"

        def take_snapshot():
            if tombstone:
                snapshot = {"__deleted__": True}
            else:
                snapshot = self._serialize_fields()
                if self.is_deleted:
                    snapshot["__deleted__"] = True
            return snapshot, self._compute_hash(snapshot)

        def unchanged(last, data_hash):
            return not tombstone and last and last.data_hash == data_hash and action not in ("delete", "restore", "rollback")

        # Decide whether a version is needed before opening a transaction, so the
        # common "nothing changed" case (e.g. a cached related object) stays cheap
        last = self._get_last_version()
        snapshot, data_hash = take_snapshot()
        if unchanged(last, data_hash):
            return

        # No savepoint: any failure propagates and rolls back the caller's transaction anyway
        with transaction.atomic(using=self._db_alias(), savepoint=False):
            if not _locked:
                # Lock the row so concurrent writers can't both claim the same version number.
                # Then re-read the latest version and re-take the snapshot: writers that held
                # the lock before us have committed, and the snapshot must include their changes.
                self._lock_rows([self.pk], using=self._db_alias())
                last = self._get_last_version()
                snapshot, data_hash = take_snapshot()
                if unchanged(last, data_hash):
                    return
            if not last and not tombstone:
                action = "create"

            # Compute human-readable diff
            last_data = last.data if last else {}
            diff = {
                k: {"old": last_data.get(k), "new": v}
                for k, v in snapshot.items() if last_data.get(k) != v
            }

            # Save VersionControl entry
            version_number = (last.version + 1) if last else 1
            VersionControl.objects.create(
                content_type=ContentType.objects.get_for_model(self.__class__),
                object_id=self.pk,
                version=version_number,
                data=snapshot,
                data_hash=data_hash,
                action=action,
                diff=diff,
                user=user or self._get_request_user()
            )

            # Update current_version with a queryset update so no extra pre/post_save signals fire
            self.current_version = version_number
            self.__class__._base_manager.filter(pk=self.pk).update(current_version=version_number)

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
        with transaction.atomic(using=self._db_alias(kwargs.get("using"))):
            super().save(*args, **kwargs)
            self.save_version(user=user, _locked=True)

    def delete(self, user=None, hard=False, *args, **kwargs):
        """
        Delete a record.

        If `hard=True`, a tombstone version is recorded and the row is then removed from the database.
        Otherwise, the record is marked as deleted and a version control snapshot is saved with action "delete".

        Soft-deleting a record that is already soft-deleted does nothing.

        :param user: User who initiated the delete; resolved from the active request if omitted
        :param hard: If True, permanently remove the row (default: False)
        :return: None
        """
        if self.is_deleted and not hard:
            return
        user = user or self._get_request_user()
        using = kwargs.get("using")
        with transaction.atomic(using=self._db_alias(using)):
            if hard:
                self.save_version(user=user, tombstone=True)
                return super().delete(*args, **kwargs)

            self.is_deleted = True
            self.deleted_at = timezone.now()
            self.deleted_by = user
            # Model.delete() arguments (keep_parents) don't apply to save(); only forward the database
            super().save(using=using)
            self.save_version(user=user, tombstone=True, action="delete", _locked=True)

    def restore(self, user=None, *args, **kwargs):
        """
        Restore a deleted record.

        If the record is not deleted, do nothing.

        After restoring, save the record and save a version control snapshot with action "restore".

        :param user: User who initiated the restore
        :return: None
        """
        if not self.is_deleted:
            return
        with transaction.atomic(using=self._db_alias(kwargs.get("using"))):
            self.is_deleted = False
            self.deleted_at = None
            self.deleted_by = None
            super().save(*args, **kwargs)
            self.save_version(user=user, action="restore", _locked=True)

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
        snapshot = VersionControl.objects.get_version(self, version_number)
        if not snapshot:
            raise ValueError("Version not found")

        data = snapshot.data
        deleted = bool(data.get("__deleted__"))

        with transaction.atomic(using=self._db_alias(kwargs.get("using"))):
            if deleted:
                # Tombstone snapshots hold no field data; rolling back to one soft-deletes the record
                self.is_deleted = True
                self.deleted_at = timezone.now()
                self.deleted_by = user or self._get_request_user()
            else:
                for field in self._meta.fields:
                    if field.primary_key or field.name in UNVERSIONED_FIELDS:
                        continue
                    # Fields added after this snapshot was taken keep their current value
                    if field.name not in data:
                        continue
                    # attname so FKs take the stored pk; to_python converts JSON strings back to dates etc.
                    setattr(self, field.attname, field.to_python(data[field.name]))
                self.is_deleted = False
                self.deleted_at = None
                self.deleted_by = None

            super().save(*args, **kwargs)

            if not deleted:
                # Suppress the m2m_changed handler so restoring relations doesn't record extra "update" versions
                self._skip_m2m_versioning = True
                try:
                    for field in self._meta.many_to_many:
                        if field.name in data:
                            getattr(self, field.name).set(data[field.name])
                finally:
                    self._skip_m2m_versioning = False

            self.save_version(user=user, action="rollback", _locked=True)

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
                    },
                    "m2m_field_name": {
                        "added": [pk, ...],
                        "removed": [pk, ...]
                    }
                }

        Raises:
            ValueError: If either version is not found for self.
        """
        snapshots = dict(
            VersionControl.objects.filter(
                content_type=ContentType.objects.get_for_model(self.__class__),
                object_id=self.pk,
                version__in=[v1, v2],
            ).values_list("version", "data")
        )

        missing_versions = [v for v in (v1, v2) if v not in snapshots]
        if missing_versions:
            raise ValueError(
                f"Version(s) {missing_versions} not found for "
                f"{self.__class__.__name__} with ID {self.pk}"
            )

        return self._diff_snapshots(snapshots[v1], snapshots[v2])


    # ----------------------------
    # Audit trail
    # ----------------------------
    def audit_trail(self):
        """
        Generate a human-readable audit trail for this object.

        Returns a list of dictionaries, one per version that changed something
        (the creation is always included). Each dictionary contains the following keys:
            - version (int): The version number of the snapshot.
            - action (str): The action recorded for the version.
            - changed_by (str): The username of the user who performed the action, or None.
            - changed_at (datetime): The timestamp when the version was created.
            - changes (dict): A dictionary containing the changed fields and their old and new values.
        """
        versions = VersionControl.objects.filter(
            content_type=ContentType.objects.get_for_model(self.__class__),
            object_id=self.pk
        ).select_related("user").order_by("version")

        report = []
        previous_data = None
        for v in versions:
            changes = self._diff_snapshots(previous_data or {}, v.data)
            if changes or previous_data is None:
                report.append({
                    "version": v.version,
                    "action": v.action,
                    "changed_by": v.user.get_username() if v.user else None,
                    "changed_at": v.created_at,
                    "changes": changes
                })
            previous_data = v.data

        return report
