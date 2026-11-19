"""Sanitized errors shared by application policies and adapters."""


class AppError(Exception):
    def __init__(self, message: str, status: int | None = None, uncertain: bool = False):
        super().__init__(message)
        self.status = status
        self.uncertain = uncertain
