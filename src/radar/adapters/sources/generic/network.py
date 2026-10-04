"""Top-level URL guard; pinning and nested-resource isolation are transport gates."""

from ipaddress import ip_address
from typing import Callable
from urllib.parse import urlsplit

from .model import Code, PinnedTarget, SourceFailure


class URLGuard:
    def __init__(self, resolver: Callable[[str], tuple[str, ...]]):
        self.resolver = resolver

    def pin(self, url: str, *, allowed_hosts: tuple[str, ...]) -> PinnedTarget:
        try:
            if not isinstance(url, str) or len(url) > 4096 or any(ord(c) < 33 for c in url) or '\\' in url:
                raise ValueError
            parts = urlsplit(url)
            host = parts.hostname or ''
            # Exact ASCII host allowlist, no user-info, non-TLS, fragments or IP literals.
            if (parts.scheme != 'https' or parts.username is not None or parts.password is not None
                    or parts.port not in (None, 443) or parts.fragment or not host.isascii()
                    or host != host.rstrip('.') or host not in allowed_hosts
                    or host == 'localhost' or host.endswith(('.local', '.localhost', '.internal'))):
                raise ValueError
            try:
                ip_address(host)
            except ValueError:
                pass
            else:
                raise ValueError
            ips = tuple(self.resolver(host))
            if not ips or len(ips) > 16:
                raise ValueError
            for address in ips:
                parsed = ip_address(address)
                # Reject mapped/transition IPv6 as well as private, metadata,
                # unspecified, multicast, reserved and shared address spaces.
                if (not parsed.is_global or parsed.is_multicast or parsed.is_reserved
                        or getattr(parsed, 'ipv4_mapped', None) is not None
                        or getattr(parsed, 'sixtofour', None) is not None
                        or getattr(parsed, 'teredo', None) is not None):
                    raise ValueError
            return PinnedTarget(hostname=host, ips=ips, url=url)
        except Exception:
            raise SourceFailure(Code.NETWORK) from None

    @staticmethod
    def check_peer(target: PinnedTarget, peer_ip: str | None) -> None:
        try:
            if peer_ip is None or ip_address(peer_ip) not in tuple(ip_address(i) for i in target.ips):
                raise ValueError
        except ValueError:
            raise SourceFailure(Code.NETWORK) from None
