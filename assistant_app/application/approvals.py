"""Single-use approval lifecycle, independent of UI, provider and API transport."""

import json
import threading
import time
from dataclasses import replace
from uuid import uuid4

from assistant_app.domain.errors import AppError
from assistant_app.domain.models import JsonObject, Proposal, RuntimeLimits


class ApprovalService:
    """Engine operation lock precedes this lock; never hold it over network I/O."""

    def __init__(self, limits: RuntimeLimits, clock=time.monotonic):
        self.limits = limits
        self.clock = clock
        self._lock = threading.RLock()
        self._records: dict[str, Proposal] = {}
        self._unknown = False

    def reset(self) -> None:
        with self._lock:
            self._records.clear()
            self._unknown = False

    def _available(self) -> None:
        if self._unknown:
            raise AppError(
                "A prior write has an unknown outcome. Inspect the destination before starting a new session."
            )

    def _expire(self) -> None:
        for key, proposal in list(self._records.items()):
            if proposal.state == "pending" and self.clock() - proposal.created >= self.limits.proposal_ttl_seconds:
                self._records[key] = replace(
                    proposal, state="expired", outcome="Proposal expired. Request a fresh proposal."
                )

    def records(self) -> list[Proposal]:
        with self._lock:
            self._expire()
            return list(self._records.values())

    def ensure_available(self) -> None:
        with self._lock:
            self._available()

    def propose(self, action: str, prepared: JsonObject, session: str, config: str) -> Proposal:
        payload = json.dumps(prepared, ensure_ascii=False, sort_keys=True, allow_nan=False)
        with self._lock:
            self._available()
            self._expire()
            for record in self._records.values():
                if (
                    record.state == "pending"
                    and record.session == session
                    and record.config == config
                    and record.action == action
                    and record.payload_json == payload
                ):
                    return record
            if sum(p.state == "pending" for p in self._records.values()) >= self.limits.max_pending_proposals:
                raise AppError("Review existing proposals before creating more.")
            record = Proposal(uuid4().hex, action, payload, session, config, self.clock())
            self._records[record.id] = record
            return record

    def consume(self, proposal_id: str, session: str, config: str) -> Proposal:
        with self._lock:
            self._available()
            self._expire()
            proposal = self._records.get(proposal_id)
            if not proposal or proposal.state != "pending" or proposal.session != session or proposal.config != config:
                raise AppError(
                    "This proposal is unavailable, expired, or belongs to a different session/configuration."
                )
            self._records[proposal_id] = replace(proposal, state="executing")
            return proposal

    def finish(self, proposal_id: str, state: str, outcome: str) -> None:
        if state not in {"succeeded", "failed", "unknown"}:
            raise ValueError("Only terminal outcomes may finish an executing proposal.")
        with self._lock:
            proposal = self._records.get(proposal_id)
            if not proposal or proposal.state != "executing":
                raise AppError("Only an executing proposal can receive an outcome.")
            self._records[proposal_id] = replace(proposal, state=state, outcome=outcome)
            self._unknown |= state == "unknown"

    def reject(self, proposal_id: str) -> Proposal:
        with self._lock:
            self._expire()
            proposal = self._records.get(proposal_id)
            if not proposal or proposal.state != "pending":
                raise AppError("This proposal is no longer pending.")
            self._records[proposal_id] = replace(proposal, state="rejected", outcome="Rejected; nothing was written.")
            return proposal
