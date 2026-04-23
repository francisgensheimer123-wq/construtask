from django.conf import settings


class DefaultPaginationMixin:
    paginate_by = None

    def get_paginate_by(self, queryset):
        if self.paginate_by:
            return self.paginate_by
        return max(int(getattr(settings, "CONSTRUTASK_LIST_PAGE_SIZE", 20) or 20), 1)
