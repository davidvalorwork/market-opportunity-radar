"""Nonblocking socket/TLS operations under one monotonic deadline and cancellation."""
import errno
import selectors
import socket
import ssl
from time import monotonic

from radar.adapters.sources.generic.model import Code, SourceFailure


class Budget:
    def __init__(self, seconds, cancelled):
        self.deadline, self.cancelled = monotonic() + seconds, cancelled

    def check(self):
        if self.cancelled():
            raise SourceFailure(Code.CANCELLED)
        remaining = self.deadline - monotonic()
        if remaining <= 0:
            raise SourceFailure(Code.TIMEOUT)
        return remaining

    def wait(self, sock, event):
        with selectors.DefaultSelector() as selector:
            selector.register(sock, event)
            while True:
                if selector.select(min(0.05, self.check())):
                    self.check()
                    return

    def ssl_call(self, sock, function):
        while True:
            self.check()
            try:
                result = function()
                self.check()
                return result
            except ssl.SSLWantReadError:
                self.wait(sock, selectors.EVENT_READ)
            except ssl.SSLWantWriteError:
                self.wait(sock, selectors.EVENT_WRITE)


def connect(sock, address, budget):
    sock.setblocking(False)
    budget.check()
    code = sock.connect_ex(address)  # Address is a numeric IP, never a DNS hostname.
    pending = {errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY,
               getattr(errno, 'WSAEWOULDBLOCK', 10035), getattr(errno, 'WSAEINPROGRESS', 10036)}
    if code not in pending and code != 0:
        raise OSError('connect_failed')
    if code:
        budget.wait(sock, selectors.EVENT_WRITE)
        if sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR):
            raise OSError('connect_failed')
    budget.check()


def send(sock, data, budget):
    remaining = memoryview(data)
    while remaining:
        written = budget.ssl_call(sock, lambda: sock.send(remaining))
        if not written:
            raise OSError('send_failed')
        remaining = remaining[written:]


class GuardedStream:
    """HTTPResponse fp, without blocking makefile or a restarted per-read timeout.

    Application bytes are counted on recv; line/header/framing caps are enforced
    incrementally, before the standard parser sees a complete oversized header.
    """
    def __init__(self, sock, budget, *, max_wire, max_headers, max_line, read_ahead):
        self.sock, self.budget = sock, budget
        self.max_wire, self.max_headers, self.max_line = max_wire, max_headers, max_line
        self.read_ahead = read_ahead
        self.buffer = bytearray()
        self.received = self.lines = self.header_lines = 0
        self.headers = True
        self.closed = False

    def makefile(self, mode):
        if mode != 'rb':
            raise SourceFailure(Code.INVALID)
        return self

    def _receive(self,maximum=4096):
        if self.received >= self.max_wire:
            raise SourceFailure(Code.LIMIT)
        chunk = self.budget.ssl_call(self.sock, lambda: self.sock.recv(min(maximum,4096,self.max_wire-self.received)))
        self.received += len(chunk)
        self.buffer.extend(chunk)
        return bool(chunk)

    def readline(self, limit=-1):
        limit = self.max_line + 1 if limit < 0 else min(limit, self.max_line + 1)
        result = bytearray()
        while True:
            self.budget.check()
            if not self.buffer:
                # One bounded buffer, including possible body prefetch. Never
                # acquire past line/header cap + one sentinel or global budget.
                maximum = min(self.read_ahead,limit-len(result),
                              self.max_headers-self.lines-len(result)+1)
                if maximum <= 0:
                    raise SourceFailure(Code.LIMIT)
                if not self._receive(maximum):
                    break
            newline = self.buffer.find(b'\n')
            take = min(newline+1 if newline >= 0 else len(self.buffer), limit-len(result))
            result.extend(self.buffer[:take])
            del self.buffer[:take]
            if len(result) > self.max_line:
                raise SourceFailure(Code.LIMIT)
            if result.endswith(b'\n'):
                break
            if len(result) >= limit:
                break
        self.lines += len(result)
        if self.headers:
            self.header_lines += 1
        if self.lines > self.max_headers or self.header_lines > 64:
            raise SourceFailure(Code.LIMIT)
        if not result.endswith(b'\r\n'):
            raise SourceFailure(Code.FAILURE)
        return bytes(result)

    def read1(self, size=-1):
        self.budget.check()
        if size == 0:
            return b''
        if not self.buffer and not self._receive(4096 if size < 0 else size):
            return b''
        size = len(self.buffer) if size < 0 else min(size, len(self.buffer))
        result = bytes(self.buffer[:size])
        del self.buffer[:size]
        return result

    def read(self, size=-1):
        result = bytearray()
        while size < 0 or len(result) < size:
            chunk = self.read1(-1 if size < 0 else size-len(result))
            if not chunk:
                break
            result.extend(chunk)
        return bytes(result)

    def close(self):
        self.closed = True
        self.buffer.clear()

    def flush(self):
        # Read-only fp: HTTPResponse.close invokes flush while fp is still open.
        pass
