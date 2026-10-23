"""
A small JSON API showing django-versioning from views.

Because CrequestMiddleware is enabled, versions written here record
request.user automatically; no user= argument is needed.
"""
import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_POST

from blog.models import Article
from version_control.models import VersionControl


@login_required
@require_GET
def history(request, pk):
    """GET /articles/<pk>/history/ -> every version of the article."""
    article = get_object_or_404(Article.objects.all_with_deleted(), pk=pk)
    return JsonResponse(
        {
            "current_version": article.current_version,
            "versions": [
                {"version": v.version, "action": v.action, "user": str(v.user) if v.user else None, "data": v.data}
                for v in VersionControl.objects.history(article)
            ],
        }
    )


@login_required
@require_GET
def audit(request, pk):
    """GET /articles/<pk>/audit/ -> who changed what, and when."""
    article = get_object_or_404(Article.objects.all_with_deleted(), pk=pk)
    return JsonResponse({"audit_trail": article.audit_trail()})


@login_required
@require_GET
def diff(request, pk, v1, v2):
    """GET /articles/<pk>/diff/<v1>/<v2>/ -> changes between two versions."""
    article = get_object_or_404(Article.objects.all_with_deleted(), pk=pk)
    try:
        return JsonResponse({"diff": article.diff_versions(v1, v2)})
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=404)


@login_required
@require_POST
def update(request, pk):
    """POST /articles/<pk>/update/ with a JSON body such as {"title": "..."}."""
    article = get_object_or_404(Article, pk=pk)
    for field, value in json.loads(request.body).items():
        if field in {"title", "body"}:
            setattr(article, field, value)
    article.save()  # version recorded with request.user
    return JsonResponse({"current_version": article.current_version})


@login_required
@require_POST
def rollback(request, pk, version):
    """POST /articles/<pk>/rollback/<version>/"""
    article = get_object_or_404(Article.objects.all_with_deleted(), pk=pk)
    try:
        article.rollback(version)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=404)
    return JsonResponse({"current_version": article.current_version, "title": article.title})


@login_required
@require_POST
def delete(request, pk):
    """POST /articles/<pk>/delete/ -> soft delete (add ?hard=1 to delete permanently)."""
    article = get_object_or_404(Article.objects.all_with_deleted(), pk=pk)
    article.delete(hard=request.GET.get("hard") == "1")
    return JsonResponse({"deleted": True})


@login_required
@require_POST
def restore(request, pk):
    """POST /articles/<pk>/restore/"""
    article = Article.objects.restore(pk=pk)
    if article is None:
        return JsonResponse({"error": "not found"}, status=404)
    return JsonResponse({"current_version": article.current_version})
