"""SSE 实时帧：已回放的 sequence 必须丢掉。"""
from unittest.mock import MagicMock

import pytest


@pytest.mark.parametrize(
    "last, incoming, expected",
    [
        (3, 3, False),
        (3, 2, False),
        (3, 4, True),
        (0, 1, True),
        (5, None, True),
        (5, "", True),
        (5, "4", False),
        (5, "6", True),
        (5, "x", True),
    ],
)
def test_should_emit_live_sse(last, incoming, expected):
    from app.shared.sse import should_emit_live_sse

    assert should_emit_live_sse(last, incoming) is expected


@pytest.mark.parametrize(
    "header, query, expected",
    [
        (None, None, 0),
        ("", "", 0),
        ("7", None, 7),
        (None, "4", 4),
        ("3", "9", 3),
        ("x", "5", 5),
        ("-2", None, 0),
        (" 12 ", None, 12),
    ],
)
def test_parse_last_event_id(header, query, expected):
    from app.shared.sse import parse_last_event_id

    request = MagicMock()
    request.headers.get.side_effect = lambda key, default=None: (
        header if str(key).lower() == "last-event-id" else default
    )
    request.query_params.get.side_effect = lambda key, default=None: (
        query if key == "last_event_id" else default
    )
    assert parse_last_event_id(request) == expected


@pytest.mark.parametrize(
    "sequence, after_seq, expected",
    [
        (1, 0, True),
        (5, 5, False),
        (6, 5, True),
        (4, 5, False),
    ],
)
def test_should_replay_history_event(sequence, after_seq, expected):
    from app.shared.sse import should_replay_history_event

    assert should_replay_history_event(sequence, after_seq) is expected
