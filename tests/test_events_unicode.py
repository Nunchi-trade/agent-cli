"""Unicode round-trip for the shared local telemetry log.

events.jsonl carries operator-facing payloads (wallet labels, coin names,
human notes) that routinely contain non-ASCII text. JSONLStore must read
and write it as UTF-8 explicitly: on machines whose default encoding is
not UTF-8 (e.g. Windows cp1252) the implicit locale encoding crashes
append with UnicodeEncodeError and read with UnicodeDecodeError.
"""
from __future__ import annotations

from parent.store import JSONLStore
from cli.events import append_event, read_events


NON_ASCII = "snowman \u2603 caf\u00e9 \u4e2d\u6587 \U0001f600"


def test_events_unicode_round_trip(tmp_path):
    record = append_event(str(tmp_path), {"type": "unit_unicode", "note": NON_ASCII})
    assert record["note"] == NON_ASCII
    events = read_events(str(tmp_path))
    assert len(events) == 1
    assert events[0]["note"] == NON_ASCII
    import json

    raw = (tmp_path / "events.jsonl").read_bytes()
    # ensure_ascii escapes stay ASCII-safe, but the file must decode as
    # UTF-8 and parse back to the original payload.
    assert json.loads(raw.decode("utf-8"))["note"] == NON_ASCII


def test_store_reads_utf8_written_by_others(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_bytes('{"type": "x", "note": "caf\u00e9 \u2603"}\n'.encode("utf-8"))
    store = JSONLStore(path=str(path))
    assert store.read_all() == [{"type": "x", "note": "caf\u00e9 \u2603"}]
