from collections import defaultdict

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.admin.views.main import ChangeList
from version_control.models import VersionControl
from rangefilter.filters import (
    DateRangeFilterBuilder,
)
from django.urls import reverse, NoReverseMatch
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _


def attach_resources(versions):
    """
    Load the versioned objects for `versions` with one query per content type and
    store each on its version as `_resource` (None if the object no longer exists).
    """
    ids_by_type = defaultdict(set)
    for version in versions:
        version._resource = None
        if version.content_type_id and version.object_id is not None:
            ids_by_type[version.content_type].add(version.object_id)

    for content_type, ids in ids_by_type.items():
        model_class = content_type.model_class()
        if model_class is None:  # model was removed but its content type remains
            continue
        pk_field = model_class._meta.pk
        # object_id is stored as text; convert back to the pk's type to match in_bulk() keys.
        # _base_manager so soft-deleted objects are found too.
        objects = model_class._base_manager.in_bulk([pk_field.to_python(pk) for pk in ids])
        for version in versions:
            if version.content_type_id == content_type.pk:
                version._resource = objects.get(pk_field.to_python(version.object_id))


class VersionControlChangeList(ChangeList):
    def get_results(self, request):
        super().get_results(request)
        # Evaluates the page once; the template reuses these cached instances
        attach_resources(self.result_list)


class ActionFilter(admin.SimpleListFilter):
    title = _("Action")  # Display name in sidebar
    parameter_name = "action"  # URL query parameter

    def lookups(self, request, model_admin):
        """Return list of filter options as (value, label)."""
        return [
            ("create", _("Create")),
            ("update", _("Update")),
            ("delete", _("Delete")),
            ("restore", _("Restore")),
            ("rollback", _("Rollback")),
        ]

    def queryset(self, request, queryset):
        """Filter queryset based on selected value."""
        if self.value():
            return queryset.filter(action=self.value())
        return queryset


@admin.register(VersionControl)
class VersionControlAdmin(admin.ModelAdmin):
    """
    Read-only admin for version history. Without add/change/delete permission,
    Django renders the change page as a view-only page.
    """
    date_hierarchy = "created_at"
    list_display = (
        "created_at",
        "resource",   # 👈 custom clickable link
        "version",
        "action",
        "linked_user",
    )
    list_filter = (
        ActionFilter,
        ("created_at", DateRangeFilterBuilder()),
    )
    list_select_related = ("content_type", "user")
    search_fields = ("object_id", "data_hash", f"user__{get_user_model().USERNAME_FIELD}")
    readonly_fields = (
        "content_type",
        "object_id",
        "version",
        "data",
        "data_hash",
        "action",
        "created_at",
        "user",
        "diff",
    )

    list_per_page = 20
    actions = None  # no bulk actions (including delete_selected)

    def get_changelist(self, request, **kwargs):
        return VersionControlChangeList

    # Remove add permission
    def has_add_permission(self, request):
        return False

    # Remove change permission
    def has_change_permission(self, request, obj=None):
        return False

    # Remove delete permission
    def has_delete_permission(self, request, obj=None):
        return False

    @staticmethod
    def _admin_link(instance, label):
        """Link `label` to `instance`'s admin change page, or return it as text if it isn't registered."""
        opts = instance._meta
        try:
            url = reverse(f"admin:{opts.app_label}_{opts.model_name}_change", args=[instance.pk])
        except NoReverseMatch:
            return label
        return format_html('<a href="{}">{}</a>', url, label)

    @admin.display(description=_("Resource"), ordering="content_type")
    def resource(self, obj):
        """Show linked object with app label, model name, and __str__ as a clickable link."""
        if not (obj.content_type and obj.object_id):
            return "—"
        if not hasattr(obj, "_resource"):  # outside the changelist, e.g. called directly
            attach_resources([obj])

        app_label = obj.content_type.app_label.title()
        model_class = obj.content_type.model_class()
        if model_class is None:
            return f"{app_label} | {obj.content_type.model.title()} - ID: {obj.object_id}"

        model_name = model_class._meta.verbose_name.title()
        related_obj = obj._resource
        if related_obj is None:
            return f"{app_label} | {model_name} - Deleted (ID: {obj.object_id})"

        label = f"{app_label} | {model_name} - {related_obj}"
        if getattr(related_obj, "is_deleted", False):
            # The model's own admin hides soft-deleted rows, so a link would lead nowhere
            return f"{label} (deleted)"
        return self._admin_link(related_obj, label)

    @admin.display(description=_("User"), ordering="user")
    def linked_user(self, obj):
        """Make the user a clickable link to their admin page."""
        if obj.user:
            return self._admin_link(obj.user, str(obj.user))
        return "—"
