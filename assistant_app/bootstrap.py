"""Composition root: the only application wiring that selects concrete adapters."""

from assistant_app.application.controller import ApplicationController
from assistant_app.application.engine import Engine
from assistant_app.application.read_service import ReadService
from assistant_app.infrastructure import oauth
from assistant_app.infrastructure.integrations import Integrations
from assistant_app.infrastructure.providers import complete
from assistant_app.infrastructure.storage import Store


def create_engine(store, **options):
    options.setdefault("integration_factory", lambda settings: Integrations(store, settings))
    options.setdefault("complete", complete)
    return Engine(store, **options)


def create_controller(store=None):
    store = store if store is not None else Store()
    return ApplicationController(store, create_engine(store), oauth.connect_google, oauth.list_calendars)


def create_read_service(store=None):
    # Negotiation and discovery never create storage or inspect credentials.
    # Each call observes current on-disk selections, including other processes.
    class LazyReadService:
        tool_definitions = staticmethod(ReadService.tool_definitions)

        def call(self, name, arguments):
            selected_store = store if store is not None else Store()
            return ReadService(selected_store, lambda settings: Integrations(selected_store, settings)).call(
                name, arguments
            )

    return LazyReadService()
