"""Compatibility constructor; new code uses application.engine and bootstrap."""

from assistant_app.application.builtin_tools import TOOLS as TOOLS
from assistant_app.application.engine import Engine as ApplicationEngine
from assistant_app.application.prompts import SYSTEM as SYSTEM
from assistant_app.domain.models import Proposal as Proposal


class Engine(ApplicationEngine):
    def __init__(self, store, settings=None, integrations=None, complete=None, **options):
        from assistant_app.infrastructure.integrations import Integrations
        from assistant_app.infrastructure.providers import complete as default_complete

        if integrations is None:
            options.setdefault("integration_factory", lambda selected: Integrations(store, selected))
        super().__init__(
            store, settings=settings, integrations=integrations, complete=complete or default_complete, **options
        )
