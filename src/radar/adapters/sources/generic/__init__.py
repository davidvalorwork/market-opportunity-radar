"""Generic READ-only adapters, transport-injected and disabled for live use by default."""

from .engine import Reader, Registry
from .model import (
    Code, Grant, Limits, RawItem, RawPage, ReadOperation, ReadRequest, Report,
    SourceFailure, SourceSpec,
)
from .network import URLGuard

__all__ = ['Reader', 'Registry', 'Code', 'Grant', 'Limits', 'RawItem', 'RawPage',
           'ReadOperation', 'ReadRequest', 'Report', 'SourceFailure', 'SourceSpec', 'URLGuard']
