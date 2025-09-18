from django.contrib import admin
from version_control.models import VersionControl
from rangefilter.filters import (
    DateRangeFilterBuilder,
)
from django.urls import reverse, NoReverseMatch
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

class ActionFilter(admin.SimpleListFilter):
    title = "Action"  # Display name in sidebar
    parameter_name = "action"  # URL query parameter

    def lookups(self, request, model_admin):
        """Return list of filter options as (value, label)."""
        return [
            ("create", "Create"),
            ("update", "Update"),
            ("delete", "Delete"),
            ("restore", "Restore"),
            ("rollback", "Rollback"),
        ]

    def queryset(self, request, queryset):
        """Filter queryset based on selected value."""
        if self.value():
            return queryset.filter(action=self.value())
        return queryset


@admin.register(VersionControl)
class VersionControlAdmin(admin.ModelAdmin):
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
    search_fields = ("object_id", "data_hash", "user__username")
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
    
    # Remove add permission
    def has_add_permission(self, request):
        return False
    
    # Remove change permission
    def has_change_permission(self, request, obj=None):
        return False
    
    # Remove delete permission
    def has_delete_permission(self, request, obj=None):
        return False
    
    def get_actions(self, request):
        """Remove all bulk actions."""
        actions = super().get_actions(request)
        # Remove all actions including delete_selected
        return {}
    
    def change_view(self, request, object_id, form_url='', extra_context=None):
        """Customize the change view to be read-only."""
        extra_context = extra_context or {}
        extra_context['show_save'] = False
        extra_context['show_save_and_continue'] = False
        extra_context['show_save_and_add_another'] = False
        extra_context['show_delete'] = False
        return super().change_view(
            request, object_id, form_url, extra_context=extra_context,
        )
    
    def changelist_view(self, request, extra_context=None):
        """Customize the changelist view to remove add button."""
        extra_context = extra_context or {}
        extra_context['show_add_button'] = False
        return super().changelist_view(request, extra_context=extra_context)
    
    @admin.display(description=_("Resource"))
    def resource(self, obj):
        """Show linked object with app label, model name, and __str__ as a clickable link."""
        if obj.content_type and obj.object_id:
            model_class = obj.content_type.model_class()
            try:
                related_obj = model_class.objects.get(pk=obj.object_id)
                url = reverse(
                    f"admin:{obj.content_type.app_label}_{obj.content_type.model}_change",
                    args=[obj.object_id],
                )
                app_label = obj.content_type.app_label.title()
                model_name = model_class._meta.verbose_name.title()
                return format_html(
                    '<a href="{}">{} | {} - {}</a>',
                    url,
                    app_label,
                    model_name,
                    related_obj,
                )
            except model_class.DoesNotExist:
                return f"{obj.content_type.app_label.title()} | {obj.content_type.model.title()} - Deleted (ID: {obj.object_id})"
        return "—"
    resource.short_description = "Resource"
    resource.admin_order_field = "content_type"

    @admin.display(description=_("User"))
    def linked_user(self, obj):
        """Make the user a clickable link to their admin page."""
        if obj.user:
            url = reverse("admin:auth_user_change", args=[obj.user.pk])
            return format_html('<a href="{}">{}</a>', url, obj.user)
        return "—"
    
    # Customize the object tools (remove add button)
    def get_object_tools(self, request, obj):
        return []
    
    # Optionally, you can also customize the view to show "View" instead of "Change"
    def get_view_on_site_url(self, obj=None):
        # If you want to provide a public view link, implement this
        return None