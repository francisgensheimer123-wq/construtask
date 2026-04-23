from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from Construtask.observability import health_check_view, readiness_check_view

urlpatterns = [
    path("health/", health_check_view, name="health_check"),
    path("ready/", readiness_check_view, name="readiness_check"),
    path("", include("Construtask.urls")),
    path(settings.CONSTRUTASK_ADMIN_URL, admin.site.urls),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
