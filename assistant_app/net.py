"""Compatibility exports; use assistant_app.infrastructure.net."""

from .infrastructure.net import MAX_BYTES, AppError, request_json

__all__ = ["request_json", "MAX_BYTES", "AppError"]
