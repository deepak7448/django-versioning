from django.urls import path

from blog import views

urlpatterns = [
    path("<int:pk>/history/", views.history),
    path("<int:pk>/audit/", views.audit),
    path("<int:pk>/diff/<int:v1>/<int:v2>/", views.diff),
    path("<int:pk>/update/", views.update),
    path("<int:pk>/rollback/<int:version>/", views.rollback),
    path("<int:pk>/delete/", views.delete),
    path("<int:pk>/restore/", views.restore),
]
