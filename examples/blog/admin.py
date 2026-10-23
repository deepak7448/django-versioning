from django.contrib import admin, messages
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from django.utils.html import format_html

from blog.models import Article, Author, Page, Tag


class VersionedAdmin(admin.ModelAdmin):
    """
    Admin for VersionedModel subclasses:
    - shows soft-deleted rows (filter on "is deleted") so they can be restored,
    - "Delete selected" soft-deletes and records a version for each row,
    - links every row to its history in the Version Control admin.
    """

    list_filter = ("is_deleted",)
    readonly_fields = ("current_version", "is_deleted", "deleted_at", "deleted_by")
    actions = ["restore_selected", "hard_delete_selected"]

    def get_queryset(self, request):
        # The default manager hides soft-deleted rows; include them here
        return self.model.objects.all_with_deleted()

    @admin.display(description="History")
    def history(self, obj):
        content_type = ContentType.objects.get_for_model(obj)
        url = reverse("admin:version_control_versioncontrol_changelist")
        return format_html('<a href="{}?content_type={}&object_id={}">v{}</a>', url, content_type.pk, obj.pk, obj.current_version)

    @admin.action(description="Restore selected soft-deleted rows")
    def restore_selected(self, request, queryset):
        rows = queryset.filter(is_deleted=True)
        for obj in rows:
            obj.restore(user=request.user)
        self.message_user(request, f"Restored {len(rows)} row(s).", messages.SUCCESS)

    @admin.action(description="Permanently delete selected rows")
    def hard_delete_selected(self, request, queryset):
        count, _ = queryset.hard_delete(user=request.user)
        self.message_user(request, f"Permanently deleted {count} row(s).", messages.WARNING)


@admin.register(Article)
class ArticleAdmin(VersionedAdmin):
    list_display = ("title", "author", "is_deleted", "history")
    search_fields = ("title",)


@admin.register(Author, Tag)
class NamedAdmin(VersionedAdmin):
    list_display = ("name", "is_deleted", "history")


@admin.register(Page)
class PageAdmin(VersionedAdmin):
    list_display = ("slug", "is_deleted", "history")
