"""Authored family-specific costs inside the disposable offline browser rig."""

import json
import uuid
from typing import Literal

from enloq.domain.models import Model, Refused
from enloq.inference.work import FLAT_UNKNOWN, budget_day
from pydantic import Field


class BudgetFixture(Model):
    action: Literal["setup", "record"] = "setup"
    account_id: int = Field(default=1, strict=True, ge=1, le=2)
    family: Literal["daily", "group_briefing"] = "group_briefing"
    amount_nanos: int = Field(default=0, strict=True, ge=0)
    status: Literal["receipted", "unknown", "refused"] = "receipted"
    daily_limit_nanos: int = Field(
        default=1_000_000_000, strict=True, ge=0, le=10_000_000_000
    )
    group_limit_nanos: int = Field(
        default=2_000_000_000, strict=True, ge=0, le=10_000_000_000
    )


def register(server):
    @server.post("/api/test/group-budget")
    async def fixture(body: BudgetFixture):
        manager = server.state.accounts
        if manager.settings.mode != "offline":
            raise Refused("Authored budget fixture requires disposable offline data")
        store = manager.get(body.account_id).store
        if body.action == "setup":
            # Only this fixture's authored costs are reset; ordinary jobs and
            # other fixtures' budget evidence remain intact.
            with manager.registry.transaction() as db:
                for table in ("model_reservations", "model_receipts"):
                    db.execute(f"DELETE FROM {table} WHERE id LIKE 'gb-fixture-%'")
            for runtime in manager.runtimes.values():
                with runtime.assistant.store.transaction() as db:
                    for table in ("reservations", "receipts"):
                        db.execute(f"DELETE FROM {table} WHERE id LIKE 'gb-fixture-%'")
            manager.registry.set_budget(body.daily_limit_nanos)
            manager.registry.set_budget(body.group_limit_nanos, family="group_briefing")
        else:
            identity = "gb-fixture-" + uuid.uuid4().hex
            role = (
                "group_briefing"
                if body.family == "group_briefing"
                else "outgoing_rewrite"
            )
            actual = (
                body.amount_nanos
                if body.status == "receipted"
                else 0
                if body.status == "refused"
                else None
            )
            with store.transaction() as db:
                day = budget_day(db, store, "shared.metadata")
                db.execute(
                    "INSERT OR REPLACE INTO shared.metadata VALUES ('budget_day',?)",
                    (day,),
                )
                values = (
                    identity,
                    "day:" + day,
                    "authored-budget",
                    FLAT_UNKNOWN,
                    actual,
                    body.status,
                    store.clock(),
                    body.family,
                )
                db.execute(
                    "INSERT INTO reservations (id,bucket,engine,reserved,actual,status,created,family) VALUES (?,?,?,?,?,?,?,?)",
                    values,
                )
                db.execute(
                    "INSERT INTO shared.model_reservations (account_id,id,scope,role,input_revision,bucket,engine,reserved,actual,status,created,family) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        body.account_id,
                        identity,
                        "authored-budget",
                        role,
                        identity,
                        *values[1:],
                    ),
                )
                if body.status != "unknown":
                    receipt = (
                        identity,
                        role,
                        "authored-budget-model",
                        "authored-budget-provider",
                        json.dumps({"authored_fixture": True}),
                        None,
                        body.family,
                    )
                    db.execute(
                        "INSERT INTO receipts (id,role,model,provider,usage,error,family) VALUES (?,?,?,?,?,?,?)",
                        receipt,
                    )
                    db.execute(
                        "INSERT INTO shared.model_receipts (account_id,id,role,model,provider,usage,error,family) VALUES (?,?,?,?,?,?,?,?)",
                        (body.account_id, *receipt),
                    )
        return {
            "daily": manager.registry.budget(timezone=manager.settings.timezone),
            "group": manager.registry.budget(
                timezone=manager.settings.timezone, family="group_briefing"
            ),
        }
