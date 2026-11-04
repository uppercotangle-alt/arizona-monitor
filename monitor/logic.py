"""Server status transition detector. No alerts from stale/duplicate API readings."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .api import Snapshot
from .config import SERVERS


@dataclass(frozen=True, slots=True)
class Transition:
    server_id: int
    name: str
    kind: str  # lost or restored
    detected_at: datetime
    downtime_seconds: int | None = None


@dataclass(slots=True)
class _Track:
    stable: bool | None = None
    candidate: bool | None = None
    count: int = 0
    candidate_since: datetime | None = None
    last_source_update: datetime | None = None
    last_seen_at: datetime | None = None
    unavailable_since: datetime | None = None


class TransitionTracker:
    def __init__(self, *, max_age_seconds: int = 180, confirmations: int = 2,
                 rebaseline_after_seconds: int = 600):
        self.max_age_seconds = max_age_seconds
        self.confirmations = confirmations
        self.rebaseline_after_seconds = rebaseline_after_seconds
        self._states = {sid: _Track() for sid in SERVERS}

    def update(self, snap: Snapshot, now: datetime) -> list[Transition]:
        if snap.probable_source_outage:
            return []
        events: list[Transition] = []
        for sid in SERVERS:
            item = snap.servers.get(sid)
            if item is None or not item.is_fresh(now, self.max_age_seconds):
                continue
            state = self._states[sid]
            if state.last_source_update is not None:
                if item.updated_at == state.last_source_update:
                    continue  # re-fetched response is NOT independent confirmation
                if item.updated_at < state.last_source_update:
                    self._states[sid] = state = _Track()  # provider clock/reset
            if (state.last_seen_at is not None
                    and (now - state.last_seen_at).total_seconds() > self.rebaseline_after_seconds):
                self._states[sid] = state = _Track()  # avoid alerts after long outage
            state.last_source_update = item.updated_at
            state.last_seen_at = now
            if state.stable is None:
                state.stable = item.responding
                if not item.responding:
                    state.unavailable_since = now
                continue  # initial observation is baseline, not a new incident
            if item.responding == state.stable:
                state.candidate = None
                state.candidate_since = None
                state.count = 0
                continue
            if state.candidate == item.responding:
                state.count += 1
            else:
                state.candidate = item.responding
                state.count = 1
                state.candidate_since = now
            if state.count < self.confirmations:
                continue
            kind = "restored" if item.responding else "lost"
            downtime = None
            if kind == "lost":
                state.unavailable_since = state.candidate_since or now
            elif state.unavailable_since is not None:
                downtime = max(0, round((now - state.unavailable_since).total_seconds()))
                state.unavailable_since = None
            state.stable = item.responding
            state.candidate = None
            state.candidate_since = None
            state.count = 0
            events.append(Transition(sid, SERVERS[sid], kind, now, downtime))
        return events
