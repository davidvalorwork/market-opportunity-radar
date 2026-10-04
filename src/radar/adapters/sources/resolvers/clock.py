"""Immutable per-request deadline/cancel binding, not a resettable timeout."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import monotonic

from radar.adapters.sources.generic.model import Code, SourceFailure


@dataclass(frozen=True)
class Deadline:
    utc: datetime
    monotonic_end: float
    cancelled: object = field(repr=False)

    def remaining(self):
        try:
            cancelled=self.cancelled()
        except Exception:
            raise SourceFailure(Code.FAILURE) from None
        if type(cancelled) is not bool:
            raise SourceFailure(Code.INVALID)
        if cancelled:
            raise SourceFailure(Code.CANCELLED)
        seconds=min(self.monotonic_end-monotonic(),(self.utc-datetime.now(timezone.utc)).total_seconds())
        if seconds<=0:
            raise SourceFailure(Code.TIMEOUT)
        return seconds
