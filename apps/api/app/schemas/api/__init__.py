"""Request/response DTOs of the REST API, one module per resource."""

from app.schemas.api.common import APIModel, Email, ErrorResponse, Page

__all__ = ["APIModel", "Email", "ErrorResponse", "Page"]
