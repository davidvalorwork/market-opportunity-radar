"""Owner's social inboxes through OpenCLI and the owner's logged-in Chrome (Agent Reach route).

- X DMs: local plugin workers/opencli/x-dm (threads, read, send ONE exact message to ONE thread).
- Messenger / Instagram DMs: local read-only plugins workers/opencli/{messenger,instagram-dm}.
- Facebook Marketplace: built-in `facebook marketplace-inbox`, read-only (no thread ids, latest snippet).
Background windows, tabs closed after each call. Never logs in, never types passcodes: a locked
inbox surfaces as SocialError('dm_locked') for the owner to unlock in Chrome.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess

BROWSER = ['--window', 'background', '--keep-tab', 'false']
DM_PLUGINS = {'x': 'x-dm', 'messenger': 'messenger', 'instagram': 'instagram-dm'}
LABELS = {'x': 'X', 'messenger': 'Messenger', 'instagram': 'Instagram', 'marketplace': 'Marketplace'}


class SocialError(Exception):
    pass


def _ts(value):
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')) if value else None
    except ValueError:
        return None


class Social:
    def __init__(self, *, node, script, runner=subprocess.run):
        self.node, self.script, self.runner = str(node), str(script), runner
        if not Path(self.node).is_file() or not Path(self.script).is_file():
            raise ValueError('opencli_missing')

    def _run(self, args, timeout=180):
        result = self.runner([self.node, self.script, *args, '-f', 'json', *BROWSER], capture_output=True, timeout=timeout,
            stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        out = result.stdout.decode('utf-8', 'replace')
        if result.returncode:
            found = re.search(r'code:\s*"?([A-Za-z_]+)', out + result.stderr.decode('utf-8', 'replace'))
            raise SocialError(found.group(1) if found else 'opencli_failed')
        try:
            return json.loads(out)
        except ValueError:
            raise SocialError('opencli_output_invalid') from None

    def threads(self, platform, limit=20):
        if platform == 'marketplace':
            return self.marketplace(limit)
        return [{**row, 'platform': platform} for row in self._run([DM_PLUGINS[platform], 'dm-threads', '--limit', str(limit)])]

    def read(self, platform, thread_id, limit=30):
        return [{**row, 'platform': platform, 'ts': _ts(row.get('time'))}
            for row in self._run([DM_PLUGINS[platform], 'dm-read', thread_id, '--limit', str(limit)])]

    def x_threads(self, limit=20):
        return self.threads('x', limit)

    def x_read(self, thread_id, limit=30):
        return self.read('x', thread_id, limit)

    def x_send(self, thread_id, text):
        """-> ('sent'|'failed', detail). One message, one thread, never retried."""
        try:
            result = self._run(['x-dm', 'dm-send', thread_id, text, '--yes'], timeout=240)
        except (SocialError, subprocess.TimeoutExpired, OSError) as error:
            return 'failed', str(error) or type(error).__name__
        result = result[0] if isinstance(result, list) and result else result
        if isinstance(result, dict) and result.get('sent'):
            return 'sent', '' if result.get('verified') else 'sin confirmación de lectura'
        return 'failed', 'not_sent'

    # source -> (opencli site, extra flags). Results normalize to Google-like rows: (url, b'Title: ...\n<text>').
    SEARCH = {'google': ('google', ['--lang', 'es']), 'yahoo': ('yahoo', []), 'facebook': ('facebook', []),
        'instagram': ('instagram', []), 'x': ('twitter', []), 'youtube': ('youtube', []), 'reddit': ('reddit', []),
        'threads': ('threads', []), 'tiktok': ('tiktok', [])}
    PROFILE_URL = {'instagram': 'https://www.instagram.com/', 'tiktok': 'https://www.tiktok.com/@', 'x': 'https://x.com/'}

    def search(self, source, query, limit=10, timeout=90):
        site, extra = self.SEARCH[source]
        words = [w for w in query.split() if len(w) > 3][:2]
        retry = source in ('instagram', 'tiktok') and len(words) == 2 and ' '.join(words) != query
        try:
            rows = self._run([site, 'search', query, '--limit', str(limit), *extra], timeout=timeout)
        except SocialError:
            if not retry:
                raise
            rows = []
        if retry and not rows:
            # Account search matches names: "taller de mofles Caracas" finds nothing, "taller mofles" does.
            rows = self._run([site, 'search', ' '.join(words), '--limit', str(limit), *extra], timeout=timeout)
        found = []
        for row in rows if isinstance(rows, list) else ():
            if not isinstance(row, dict):
                continue
            handle = row.get('username') or row.get('user') or row.get('author') or row.get('channel') or ''
            url = row.get('url') or (self.PROFILE_URL[source] + str(handle) if source in self.PROFILE_URL and handle else '')
            title = row.get('title') or row.get('full_name') or row.get('name') or str(handle)
            text = ' · '.join(str(row[k]) for k in ('text', 'snippet', 'selftext', 'bio', 'biography', 'description') if row.get(k))
            if str(url).startswith('http'):
                found.append((str(url), ('Title: ' + str(title)[:200] + '\n' + text[:3000]).encode('utf-8')))
        return found

    def marketplace(self, limit=20):
        return [{'platform': 'marketplace', 'thread_id': None, 'name': row.get('buyer') or '', 'listing': row.get('listing') or '',
            'last_text': row.get('snippet') or '', 'last_time': row.get('time'), 'unread': bool(row.get('unread'))}
            for row in self._run(['facebook', 'marketplace-inbox', '--limit', str(limit)])]


if __name__ == '__main__':
    calls = []
    def fake(argv, **kw):
        calls.append(argv)
        replies = {'dm-threads': b'[{"thread_id":"t1","name":"Ana","last_text":"hola","last_time":null,"unread":true}]',
            'dm-read': b'[{"thread_id":"t1","message_id":null,"author":"Ana","is_me":false,"text":"hola","time":"2026-10-05T20:00:00Z","media":null}]',
            'dm-send': b'[{"sent":true,"thread_id":"t1","verified":true}]',
            'marketplace-inbox': b'[{"index":1,"buyer":"Luis","listing":"Silla","snippet":"sigue disponible?","time":"2h","unread":true}]',
            'search': b'[{"username":"taller.x","full_name":"Taller X","biography":"Escapes 0414-123.45.67"}]'}
        key = next(k for k in replies if k in argv)
        return type('R', (), {'returncode': 0, 'stdout': replies[key], 'stderr': b''})()
    social = Social(node=__file__, script=__file__, runner=fake)
    assert social.x_threads()[0]['platform'] == 'x' and social.x_read('t1')[0]['ts'].year == 2026
    assert social.x_send('t1', 'ok') == ('sent', '') and calls[-1][-6:] == ['--yes', '-f', 'json', '--window', 'background', '--keep-tab', 'false'][-6:]
    assert social.search('instagram', 'taller')[0][0] == 'https://www.instagram.com/taller.x'
    assert social.marketplace()[0]['listing'] == 'Silla' and social.threads('marketplace')[0]['platform'] == 'marketplace'
    assert social.threads('instagram')[0]['platform'] == 'instagram' and calls[-1][2] == 'instagram-dm'
    assert social.read('messenger', 't1')[0]['platform'] == 'messenger' and calls[-1][2] == 'messenger'
    locked = lambda argv, **kw: type('R', (), {'returncode': 1, 'stdout': b'ok: false\nerror:\n  code: dm_locked\n', 'stderr': b''})()
    try:
        Social(node=__file__, script=__file__, runner=locked).x_threads(); raise AssertionError
    except SocialError as error:
        assert str(error) == 'dm_locked'
    assert Social(node=__file__, script=__file__, runner=locked).x_send('t1', 'x') == ('failed', 'dm_locked')
    print('ok')
