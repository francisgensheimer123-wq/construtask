from django.conf import settings


class DefaultPaginationMixin:
    paginate_by = None

    def get_paginate_by(self, queryset):
        if self.paginate_by:
            return self.paginate_by
        return max(int(getattr(settings, "CONSTRUTASK_LIST_PAGE_SIZE", 20) or 20), 1)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        filtros = self.request.GET.copy()
        filtros.pop("page", None)
        context.setdefault("querystring_sem_pagina", filtros.urlencode())
        return context
