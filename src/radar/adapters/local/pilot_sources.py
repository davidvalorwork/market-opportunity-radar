"""Agent Reach's installed Exa read route, bounded and opt-in.

No model calls, browser sessions, shell or arbitrary tools. Exa's MCP has no
native cursor here: continuation represents discovery rounds, NOT provider pages.
Queries must be explicitly supplied as public research by the owner; this adapter
never derives them from private chats. Captures are not assertions of source truth.
"""
from datetime import timezone
import json
from pathlib import Path
import re
import subprocess
from threading import Event, Thread
from urllib.parse import urlsplit

from radar.adapters.sources.generic.model import RawItem, RawPage, SourceFailure, Code
from radar.ports.types import Capability


def bounded_call(argv, *, timeout, limit):
    """Own child only, bounded pipes throughout acquisition, static diagnostics."""
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, shell=False,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    chunks, total, overflow = [], [0], Event()
    def drain():
        while True:
            chunk = process.stdout.read(4096)
            if not chunk:
                break
            total[0] += len(chunk)
            if total[0] > limit:
                overflow.set()
                process.kill()
                break
            chunks.append(chunk)
    reader = Thread(target=drain, daemon=True)
    reader.start()
    try:
        process.wait(timeout=timeout)
        reader.join(timeout=2)
        if overflow.is_set():
            raise SourceFailure(Code.LIMIT)
        if reader.is_alive() or process.returncode:
            raise SourceFailure(Code.FAILURE)
        return b''.join(chunks)
    except subprocess.TimeoutExpired:
        raise SourceFailure(Code.TIMEOUT) from None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        reader.join(timeout=2)
        process.stdout.close()


def parse_exa(body):
    value = json.loads(body)
    if value.get('isError'):
        raise SourceFailure(Code.FAILURE)
    text = '\n'.join(row.get('text', '') for row in value.get('content', ()) if row.get('type') == 'text')
    results = []
    for block in re.split(r'(?m)(?=^Title: )', text):
        found = re.search(r'(?m)^URL: (https://[^\s]+)\s*$', block)
        if not found:
            continue
        url = found.group(1)
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            continue
        results.append((url, block.strip().encode('utf-8')))
    if not results:
        raise SourceFailure(Code.FAILURE)
    return tuple(results)


class AgentReachExa:
    fixture_only = False
    guarded_live = True  # Static Exa read tool only; not a general CLI/browser guard.

    def __init__(self, *, node, mcporter_script, clock, runner=bounded_call):
        self.node, self.script = str(Path(node).absolute()), str(Path(mcporter_script).absolute())
        if not Path(self.node).is_file() or not Path(self.script).is_file():
            raise ValueError('agent_reach_cli_missing')
        self.clock, self.runner, self.verified_at = clock, runner, None
        self.skip_url = lambda request, url: False
        self.limit_items = lambda request: request.limits.items

    def search(self, query, count, *, timeout, max_bytes):
        if not isinstance(query, str) or not query.strip() or len(query.encode()) > 4096:
            raise SourceFailure(Code.INVALID)
        # No caller-provided executable/method/flags and no shell interpretation.
        args = [self.node, self.script, 'call', 'exa.web_search_exa',
            'query=' + query, 'numResults=' + str(min(10, count)), '--output', 'json',
            '--timeout', str(max(1000, int(timeout * 1000)))]
        raw = self.runner(args, timeout=timeout, limit=max_bytes)
        return parse_exa(raw), len(raw)

    def verify(self):
        self.search('Official Python documentation asyncio', 1, timeout=15, max_bytes=131072)
        self.verified_at = self.clock.now()

    def read(self, call, *, cancelled):
        if cancelled():
            raise SourceFailure(Code.CANCELLED)
        round_no = int(call.cursor) if call.cursor is not None else 0
        if not 0 <= round_no <= 1000:
            raise SourceFailure(Code.INVALID)
        query = call.request.query
        if round_no:
            query += '\nFind additional independent sources, alternative sites and perspectives. Discovery round ' + str(round_no + 1) + '.'
        requested = max(1, min(10, self.limit_items(call.request)))
        rows, size = self.search(query, requested, timeout=min(20, call.timeout_seconds), max_bytes=call.max_bytes)
        if cancelled():
            raise SourceFailure(Code.CANCELLED)
        items = tuple(RawItem(content, url) for url, content in rows
            if not self.skip_url(call.request, url))
        # A bounded host may request another round; never claim whole-web coverage.
        return RawPage(items=items, next_cursor=str(round_no + 1), bytes_received=size)


class PilotCapabilities:
    def __init__(self, *, owner, clock, search=None):
        self.owner, self.clock, self.search = owner, clock, search
        self.whatsapp_verified = lambda session: False
        self.whatsapp_operation_verified = lambda session, operation: False

    def get(self, *, owner_ref, platform, backend, operation, session_ref=None):
        if owner_ref != self.owner:
            return None
        verified = (platform == 'web' and backend == 'agent_reach_exa' and operation == 'search'
            and self.search is not None and self.search.verified_at is not None
            and (self.clock.now() - self.search.verified_at).total_seconds() < 86400)
        session_access = False
        if platform == 'whatsapp' and backend == 'local_wameow' and operation in ('read', 'contact', 'compose', 'send', 'sync'):
            session_access = self.whatsapp_verified(session_ref)
            verified = session_access and self.whatsapp_operation_verified(session_ref,operation)
        # For WhatsApp this is authenticated SESSION access, not evidence of a
        # completed send. Host permits only the explicit self trial before its
        # provider confirmation; subsequent exact approvals are still required.
        proof = 'proof:pairedsession' if platform == 'whatsapp' else 'proof:exaread'
        return Capability(platform, backend, operation, 'probado_real' if verified else 'documentado',
            proof if session_access or verified else 'proof:unverified', self.clock.now().astimezone(timezone.utc).date(), bool(session_access or verified))
