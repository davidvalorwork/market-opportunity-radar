"""Real IANA fallback for systems (notably Windows) without a zoneinfo database."""
from datetime import datetime, timezone
from importlib.metadata import version
import zoneinfo as iana
import pytest

from radar.adapters.local.scheduler import zoneinfo
from radar.application.schedules.model import Recurrence


UTC = timezone.utc


@pytest.fixture(autouse=True)
def package_only_zone_database():
    original = iana.TZPATH
    iana.ZoneInfo.clear_cache()
    iana.reset_tzpath(())
    try:
        yield
    finally:
        iana.ZoneInfo.clear_cache()
        iana.reset_tzpath(original)


def test_pinned_provider_and_caracas_default():
    assert version('tzdata') == '2026.4'
    start = datetime(2026, 10, 4, tzinfo=UTC)
    recurrence = Recurrence('daily', start)
    assert recurrence.next_after(start, zoneinfo) == datetime(2026, 10, 4, 13, tzinfo=UTC)


def test_iana_spring_gap_skips_nonexistent_day():
    start = datetime(2026, 3, 8, tzinfo=UTC)
    recurrence = Recurrence('daily', start, zone='America/New_York', hour=2, minute=30)
    assert recurrence.next_after(start, zoneinfo) == datetime(2026, 3, 9, 6, 30, tzinfo=UTC)


@pytest.mark.parametrize('policy,hour', [('earlier', 5), ('later', 6)])
def test_iana_fall_fold_selects_exactly_one_instant(policy, hour):
    start = datetime(2026, 11, 1, tzinfo=UTC)
    recurrence = Recurrence('daily', start, zone='America/New_York', hour=1, minute=30, ambiguous=policy)
    first = recurrence.next_after(start, zoneinfo)
    assert first == datetime(2026, 11, 1, hour, 30, tzinfo=UTC)
    assert recurrence.next_after(first, zoneinfo) == datetime(2026, 11, 2, 6, 30, tzinfo=UTC)


def test_real_london_weekly_after_autumn_transition():
    start = datetime(2026, 10, 25, tzinfo=UTC)
    recurrence = Recurrence('weekly', start, zone='Europe/London', hour=8, weekdays=(0,))
    assert recurrence.next_after(start, zoneinfo) == datetime(2026, 10, 26, 8, tzinfo=UTC)


def test_unknown_zone_stays_explicit_failure():
    with pytest.raises(ValueError, match='^timezone_unavailable$'):
        zoneinfo('Not_A_Real/Timezone')
