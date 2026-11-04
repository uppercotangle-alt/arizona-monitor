from datetime import datetime, timedelta, timezone

import pytest

from monitor.api import APIError, ServerInfo, Snapshot, parse_payload
from monitor.logic import TransitionTracker

UTC = timezone.utc
T0 = datetime(2026, 11, 4, 20, 0, tzinfo=UTC)


def item(sid: int, responding: bool, updated_at: datetime, online: int = 100) -> ServerInfo:
    return ServerInfo(sid, "Yuma" if sid == 25 else "Show Low", online if responding else 0,
                      1000, responding, updated_at)


def snap(now: datetime, *, yuma=True, show=True, updated_at=None, all_down=False):
    updated_at = updated_at or now
    return Snapshot({25: item(25, yuma, updated_at), 52: item(52, show, updated_at)},
                    now, 37, 0 if all_down else 36)


def test_parse_real_shape():
    payload = {"project": {"name": "Arizona RP"}, "servers": [
        {"id": 25, "name": "Yuma", "online": 989, "max_slots": 1000,
         "responding": True, "updated_at": "2026-11-04T20:00:00Z", "peak_day": 1000},
        {"id": 52, "name": "Show Low", "online": 334, "max_slots": 1000,
         "responding": True, "updated_at": "2026-11-04T20:00:00Z"},
    ]}
    result = parse_payload(payload, T0)
    assert result.servers[25].online == 989
    assert result.servers[52].name == "Show Low"
    assert result.servers[25].is_fresh(T0, 180)


def test_malformed_payload_rejected():
    with pytest.raises(APIError):
        parse_payload({"servers": "no"}, T0)
    with pytest.raises(APIError):
        parse_payload({"servers": [{"id": 25, "online": "999", "max_slots": 1000,
                                        "responding": True, "updated_at": "2026-11-04T20:00:00Z"}]}, T0)


def test_two_unique_confirmations_and_recovery():
    tr = TransitionTracker()
    assert tr.update(snap(T0), T0) == []
    t1 = T0 + timedelta(seconds=20)
    t2 = T0 + timedelta(seconds=40)
    t3 = T0 + timedelta(seconds=60)
    t4 = T0 + timedelta(seconds=80)
    assert tr.update(snap(t1, yuma=False), t1) == []
    assert tr.update(snap(t1, yuma=False), t1) == []  # duplicate upstream time
    events = tr.update(snap(t2, yuma=False), t2)
    assert [(e.server_id, e.kind) for e in events] == [(25, "lost")]
    assert tr.update(snap(t3), t3) == []
    events = tr.update(snap(t4), t4)
    assert [(e.server_id, e.kind) for e in events] == [(25, "restored")]
    assert events[0].downtime_seconds == 60


def test_stale_data_cannot_trigger_alert():
    tr = TransitionTracker(max_age_seconds=180)
    tr.update(snap(T0), T0)
    later = T0 + timedelta(minutes=5)
    assert tr.update(snap(later, yuma=False, updated_at=T0), later) == []
    assert tr.update(snap(later + timedelta(seconds=20), yuma=False, updated_at=T0),
                     later + timedelta(seconds=20)) == []


def test_mass_outage_suppresses_notifications():
    tr = TransitionTracker()
    tr.update(snap(T0), T0)
    t1 = T0 + timedelta(seconds=20)
    t2 = T0 + timedelta(seconds=40)
    assert tr.update(snap(t1, yuma=False, all_down=True), t1) == []
    assert tr.update(snap(t2, yuma=False, all_down=True), t2) == []


def test_lost_source_for_long_time_rebaselines():
    tr = TransitionTracker(rebaseline_after_seconds=600)
    tr.update(snap(T0), T0)
    late = T0 + timedelta(minutes=20)
    assert tr.update(snap(late, yuma=False), late) == []
    assert tr.update(snap(late + timedelta(seconds=20), yuma=False),
                     late + timedelta(seconds=20)) == []


def test_future_timestamp_not_fresh():
    info = item(25, True, T0 + timedelta(minutes=5))
    assert not info.is_fresh(T0, 180)

@pytest.mark.asyncio
async def test_http_client_assembles_chunked_json_and_limits_duplicate_requests():
    import json
    from monitor.api import APIClient

    ts = datetime.now(UTC)
    payload = {"servers": [
        {"id": 25, "name": "Yuma", "online": 912, "max_slots": 1000,
         "responding": True, "updated_at": ts.isoformat()},
        {"id": 52, "name": "Show Low", "online": 312, "max_slots": 1000,
         "responding": True, "updated_at": ts.isoformat()},
    ]}
    data = json.dumps(payload).encode()

    class FakeContent:
        async def iter_chunked(self, chunk_size):
            for start in range(0, len(data), 7):
                yield data[start:start + 7]

    class FakeResponse:
        status = 200
        content = FakeContent()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class FakeSession:
        calls = 0

        def get(self, *args, **kwargs):
            self.calls += 1
            return FakeResponse()

    session = FakeSession()
    api = APIClient(session, min_interval=4)
    first = await api.fetch()
    second = await api.fetch()
    assert first is second
    assert session.calls == 1
    assert first.servers[25].online == 912


@pytest.mark.asyncio
async def test_http_failure_not_retried_for_every_immediate_button_click():
    from monitor.api import APIClient

    class FakeResponse:
        status = 503

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class FakeSession:
        calls = 0

        def get(self, *args, **kwargs):
            self.calls += 1
            return FakeResponse()

    session = FakeSession()
    api = APIClient(session, min_interval=4)
    with pytest.raises(APIError):
        await api.fetch()
    with pytest.raises(APIError):
        await api.fetch()
    assert session.calls == 1
