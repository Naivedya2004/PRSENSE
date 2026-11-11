from rest_framework.pagination import PageNumberPagination


class StandardPagination(PageNumberPagination):
    """Page-number pagination with a capped page size to protect the database."""

    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100
