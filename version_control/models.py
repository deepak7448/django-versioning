from django.db import models
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.serializers.json import DjangoJSONEncoder
from crequest.middleware import CrequestMiddleware
from django.utils import timezone
from django.conf import settings
import hashlib, json

User = get_user_model()

class VersionControl(models.Model):
    """Stores snapshots of model changes with hash and precomputed diff."""
    content_type = models.ForeignKey(ContentType,on_delete=models.SET_NULL,null=True,help_text="Content type of the application")
    object_id = models.PositiveIntegerField(null=True,help_text="Object ID of the application")
    version = models.PositiveIntegerField(help_text="Version number of the snapshot")
    data = models.JSONField(encoder=DjangoJSONEncoder, help_text="Full snapshot of the record")  # full snapshot
    data_hash = models.CharField(max_length=64, db_index=True, help_text="SHA-256 hash of the snapshot")  # SHA-256 hash of the snapshot
    action = models.CharField(max_length=20, help_text="Action performed: create, update, delete, restore, rollback")  # Action performed
    diff = models.JSONField(null=True, blank=True, encoder=DjangoJSONEncoder, help_text="Precomputed human-readable diff")  # precomputed human-readable diff
    created_at = models.DateTimeField(auto_now_add=True, help_text="Timestamp when the version was created")
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, help_text="User who updated the version")

    class Meta:
        unique_together = ("content_type", "object_id", "version")

    def __str__(self):
        return f"{self.content_type} v{self.version} for {self.object_id}"


class VersionedModel(models.Model):
    """
    Base class with full relational versioning:
    - Fields
    - ForeignKey
    - ManyToMany
    """
    current_version = models.PositiveIntegerField(default=1, editable=False, help_text="Current version number of the record")
    is_deleted = models.BooleanField(default=False, editable=False, help_text="Indicates if the record is deleted")
    deleted_at = models.DateTimeField(null=True, blank=True, editable=False, help_text="Timestamp when the record was deleted")
    deleted_by = models.ForeignKey(
        User, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="deleted_%(class)s_set", editable=False, help_text="User who deleted the record"
    )


    class Meta:
        abstract = True

    # ----------------------------
    # Helpers
    # ----------------------------
    def _serialize_fields(self):
        data = {}
        for field in self._meta.fields:
            if field.name in ["id", "current_version", "is_deleted", "deleted_at", "deleted_by"]:
                continue
            value = getattr(self, field.name)
            if isinstance(field, models.ForeignKey):
                data[field.name] = value.pk if value else None
            elif isinstance(field, models.FileField):
                data[field.name] = value.name if value else None
            else:
                data[field.name] = value

        for field in self._meta.many_to_many:
            related_ids = list(getattr(self, field.name).values_list("id", flat=True))
            data[field.name] = related_ids
        return data
    
    def _compute_hash(self, data: dict) -> str:
        normalized = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def save_version(self, user=None, tombstone=False,action='update'):
        """Save snapshot of self + related VersionedModel objects with hash + diff."""
        # from versioning.models import VersionControl

        # ---------- 1️⃣ Save related FK/M2M objects ----------
        for field in self._meta.fields:
            if isinstance(field, models.ForeignKey):
                obj = getattr(self, field.name)
                if obj and isinstance(obj, VersionedModel):
                    obj.save_version(user=user)

        for field in self._meta.many_to_many:
            for obj in getattr(self, field.name).all():
                if isinstance(obj, VersionedModel):
                    obj.save_version(user=user)
    
        content_type = ContentType.objects.get_for_model(self.__class__)
        last = VersionControl.objects.filter(
            content_type=content_type,
            object_id=self.pk
        ).order_by("-version").first()
        if not last:  # first save = create
            action = "create"
        # ---------- 2️⃣ Prepare snapshot ----------
        if tombstone:
            snapshot = {"__deleted__": True}
            diff = {"__deleted__": True}
            action = "delete"
            data_hash = self._compute_hash(snapshot)
        else:
            snapshot = self._serialize_fields()
            if self.is_deleted:
                snapshot["__deleted__"] = True


            data_hash = self._compute_hash(snapshot)

            # Skip if no changes
            if last and last.data_hash == data_hash and action not in ("delete", "restore", "rollback"):
                return

        # ---------- 3️⃣ Human-readable diff ----------
        
        
        last_data = last.data if last else {}
        diff = {}
        for k, v in snapshot.items():
            if last_data.get(k) != v:
                diff[k] = {"old": last_data.get(k), "new": v}

        # ---------- 4️⃣ Save VersionControl ----------
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
            user=user if user else (CrequestMiddleware.get_request().user if CrequestMiddleware.get_request() and CrequestMiddleware.get_request().user.is_authenticated else None)
        )

        # Update current_version
        self.current_version = version_number
        super(VersionedModel, self).save(update_fields=["current_version"])
    # ----------------------------
    # Lifecycle methods
    # ----------------------------
    def save(self, *args, user=None, **kwargs):
        super().save(*args, **kwargs)
        self.save_version(user=user)


    def delete(self, user=None, hard=False, *args, **kwargs):
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
        if not self.is_deleted:
            return
        self.is_deleted = False
        self.deleted_at = None
        self.deleted_by = None
        self.current_version += 1        
        super().save(*args, **kwargs)
        self.save_version(user=user,action="restore")

    # ----------------------------
    # Rollback with related objects
    # ----------------------------
    def rollback(self, version_number, user=None, *args, **kwargs):
        content_type = ContentType.objects.get_for_model(self.__class__)
        snapshot = VersionControl.objects.filter(
            content_type=content_type,
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
                if field.name in ["id", "current_version", "is_deleted", "deleted_at", "deleted_by"]:
                    continue
        self.current_version = version_number
        self.is_deleted = False
        self.deleted_at = None
        self.deleted_by = None
        super().save(*args, **kwargs)
        self.save_version(user=user, action="rollback")

    # ----------------------------
    # Diff including related objects
    # ----------------------------
    def diff_versions(self, v1, v2):
        content_type = ContentType.objects.get_for_model(self.__class__)
        snap1 = VersionControl.objects.filter(
            content_type=content_type, object_id=self.pk, version=v1
        ).first()
        snap2 = VersionControl.objects.filter(
            content_type=content_type, object_id=self.pk, version=v2
        ).first()
        if not snap1 or not snap2:
            raise ValueError("One or both versions not found")

        changes = {}
        for key in set(snap1.data.keys()).union(set(snap2.data.keys())):
            old_val, new_val = snap1.data.get(key), snap2.data.get(key)
            if isinstance(old_val, list) and isinstance(new_val, list):
                # M2M diff
                added = list(set(new_val) - set(old_val))
                removed = list(set(old_val) - set(new_val))
                if added or removed:
                    changes[key] = {"added": added, "removed": removed}
            elif old_val != new_val:
                changes[key] = {"from": old_val, "to": new_val}
        return changes


    def audit_trail(self):
        content_type = ContentType.objects.get_for_model(self.__class__)
        history = VersionControl.objects.filter(
            content_type=content_type,
            object_id=self.pk
        ).order_by("version")
        report = []
        previous_data = None
        for vc in history:
            if previous_data is not None:
                changes = {}
                for key in set(previous_data.keys()).union(vc.data.keys()):
                    old_val = previous_data.get(key)
                    new_val = vc.data.get(key)
                    if isinstance(old_val, list) and isinstance(new_val, list):
                        added = list(set(new_val) - set(old_val))
                        removed = list(set(old_val) - set(new_val))
                        if added or removed:
                            changes[key] = {"added": added, "removed": removed}
                    elif old_val != new_val:
                        changes[key] = {"from": old_val, "to": new_val}
                if changes:
                    report.append({
                        "version": vc.version,
                        "changed_by": vc.user.username if vc.user else None,
                        "changed_at": vc.created_at,
                        "changes": changes
                    })
            previous_data = vc.data
        return report
