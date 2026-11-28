"""Wiring: the use cases of one serving process from its settings and its database."""

from __future__ import annotations

from fastapi import FastAPI

from onepledge_key_holder.application.authenticate_member import AuthenticateMember
from onepledge_key_holder.application.evaluate_blinded import EvaluateBlinded, HolderKeys
from onepledge_key_holder.application.report_health import ReportHealth
from onepledge_key_holder.infrastructure.db.repositories import Database
from onepledge_key_holder.infrastructure.logging_setup import get_logger
from onepledge_key_holder.infrastructure.scalar_keyring import ScalarKeyring
from onepledge_key_holder.infrastructure.settings import ServeSettings
from onepledge_key_holder.infrastructure.system import RandomIds, SystemClock
from onepledge_key_holder.interface.http import SERVICE, UseCases, create_app


def build_app(settings: ServeSettings, database: Database) -> FastAPI:
    """The app of one holder: its scalar in memory, its database behind the app role."""
    keys = HolderKeys(
        settings.holder_id,
        settings.active_key_version,
        ScalarKeyring({settings.active_key_version: settings.scalar}),
    )
    use_cases = UseCases(
        report_health=ReportHealth(settings.holder_id, settings.active_key_version),
        authenticate=AuthenticateMember(database.credentials()),
        evaluate=EvaluateBlinded(
            keys, database.journal(), SystemClock(), RandomIds(), settings.daily_element_cap
        ),
    )
    return create_app(use_cases, get_logger(SERVICE, settings.holder_id.value))
