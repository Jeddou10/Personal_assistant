"""Compatibility exports for the native desktop launcher."""

from .frontend.app import AssistantUI, main
from .frontend.approval import preview_text
from .frontend.theme import draw_cat, draw_soccer_ball

__all__ = ["AssistantUI", "main", "draw_cat", "draw_soccer_ball", "preview_text"]
