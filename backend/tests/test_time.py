"""时间字段：naive 一律按 UTC，禁止按本地时解码。"""
from datetime import datetime, timezone

from app.core.agent_runner import _parse_docker_time
from app.shared.time import iso_utc, utc_unix


def test_iso_utc_treats_naive_as_utc():
    naive = datetime(2026, 8, 18, 9, 18, 33)
    aware = datetime(2026, 8, 18, 9, 18, 33, tzinfo=timezone.utc)
    assert iso_utc(naive) == "2026-08-18T09:18:33+00:00"
    assert iso_utc(aware) == "2026-08-18T09:18:33+00:00"
    assert iso_utc(None) is None


def test_utc_unix_treats_naive_as_utc_not_local():
    naive = datetime(2026, 8, 13, 12, 19, 57)
    aware = datetime(2026, 8, 13, 12, 19, 57, tzinfo=timezone.utc)
    assert utc_unix(naive) == aware.timestamp()
    assert utc_unix(None) is None
    assert utc_unix(aware) == aware.timestamp()


def test_parse_docker_time_naive_and_zulu_are_utc():
    expected = datetime(2026, 8, 18, 9, 0, 0, tzinfo=timezone.utc).timestamp()
    assert _parse_docker_time("2026-08-18T09:00:00") == expected
    assert _parse_docker_time("2026-08-18T09:00:00Z") == expected
    assert _parse_docker_time("2026-08-18T09:00:00+00:00") == expected
