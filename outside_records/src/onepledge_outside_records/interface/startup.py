"""`serve` starts through a fixed sequence of named checks (SPEC_DECISIONS 104).

Each check has its own failure sentence. A database error raised inside a check is caught at
the check's boundary and reported as that check's failure with its SQLSTATE, so an ordinary
condition of the environment never reaches the last resort handler. When every check has
passed, new pooled connections are guarded (SPEC_DECISIONS 103.7) and the app is built.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import FastAPI

from onepledge_outside_records.infrastructure.binding import check_binding
from onepledge_outside_records.infrastructure.db.failures import DATABASE_ERRORS, startup_check_refused
from onepledge_outside_records.infrastructure.db.migrations import installed_head
from onepledge_outside_records.infrastructure.settings import ServeSettings, load_serve_settings
from onepledge_outside_records.interface.composition import Composition
from onepledge_outside_records.interface.http.app import create_app

CHECK_NAMES = (
    "settings",
    "database reachable",
    "database encoding",
    "session parameters",
    "schema in place",
    "role inspection",
    "bind probe",
)


def startup_check[T](name: str, action: Callable[[], T]) -> T:
    """Run one named check; a statement the database refuses is that check's failure."""
    try:
        return action()
    except DATABASE_ERRORS as error:
        raise startup_check_refused(name, error) from None


@dataclass(frozen=True)
class Started:
    """What the checks established: the settings, the port and the app ready to bind."""

    settings: ServeSettings
    port: int
    app: FastAPI


def start(composition: Composition) -> Started:
    """Run the checks of CHECK_NAMES in order, then build the app."""
    settings_name, reachable, encoding, session, schema, role, bind = CHECK_NAMES
    settings = startup_check(settings_name, load_serve_settings)
    port = startup_check(settings_name, settings.require_port)
    reader = composition.reader(settings)
    startup_check(reachable, reader.ensure_reachable)
    startup_check(encoding, reader.ensure_utf8)
    startup_check(session, reader.ensure_session_parameters)
    startup_check(schema, lambda: reader.ensure_at_head(installed_head()))
    startup_check(role, reader.ensure_read_only)
    startup_check(bind, lambda: check_binding(settings.bind_host, port))
    reader.guard_new_connections()
    return Started(settings, port, create_app(reader))
