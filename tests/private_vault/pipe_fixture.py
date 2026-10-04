"""Explicit hostile process fixture, NEVER registered in production wiring."""
import os
import sys
import time

body = sys.stdin.buffer.read()
if b'fixture:timeout' in body:
    time.sleep(60)
elif b'fixture:stdout' in body:
    sys.stdout.buffer.write(b'x' * 2_000_000)
elif b'fixture:stderr' in body:
    sys.stderr.buffer.write(b'SYNTHETIC_SECRET_PHONE_123' * 1000)
elif b'fixture:exit' in body:
    sys.stderr.buffer.write(b'SYNTHETIC_SECRET_PHONE_123')
    sys.exit(1)
else:
    sys.stdout.buffer.write(b'malformed private output')
