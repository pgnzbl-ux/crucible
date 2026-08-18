"""SSE 实时帧：已回放的 sequence 必须丢掉。"""
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
