"""HTTP/1 response framing and bounded private projection, no decompression."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.client import HTTPResponse
import re

from radar.adapters.sources.generic.model import Code, RawItem, RawPage, SourceFailure


def retry_after(value, now=None):
    if not isinstance(value, str) or len(value) > 128:
        return 30
    try:
        if re.fullmatch(r'[0-9]{1,10}', value.strip()):
            seconds = int(value.strip())
        else:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                return 30
            seconds = int((date - (now or datetime.now(timezone.utc))).total_seconds())
        return min(300, max(1, seconds))
    except (ValueError, TypeError, OverflowError):
        return 30


def response_page(stream, *, url, peer, max_body):
    response = HTTPResponse(stream, method='GET')
    try:
        response.begin()
        stream.headers = False
        lengths = response.headers.get_all('Content-Length', [])
        transfers = response.headers.get_all('Transfer-Encoding', [])
        if (len(lengths) > 1 or len(transfers) > 1 or (lengths and transfers)
            or any(not re.fullmatch(r'[0-9]{1,12}', v.strip()) for v in lengths)):
            raise SourceFailure(Code.NETWORK)
        if transfers and transfers[0].strip().lower() != 'chunked':
            raise SourceFailure(Code.UNSUPPORTED)
        if response.headers.get('Content-Encoding', 'identity').strip().lower() != 'identity':
            raise SourceFailure(Code.UNSUPPORTED)
        if lengths and int(lengths[0]) > max_body:
            raise SourceFailure(Code.LIMIT)
        locations = response.headers.get_all('Location', [])
        if len(locations) > 1:
            raise SourceFailure(Code.NETWORK)
        location = locations[0] if locations else None
        if response.status != 200:
            return RawPage(bytes_received=stream.received+len(url.encode('utf-8')),
                           status=response.status, peer_ip=peer,
                           redirect_url=location if response.status in (301,302,303,307,308) else None,
                           retry_after_seconds=retry_after(response.headers.get('Retry-After')))
        if location is not None:
            raise SourceFailure(Code.NETWORK)
        body = bytearray()
        while True:
            chunk = response.read1(min(4096, max_body-len(body)+1))
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > max_body:
                raise SourceFailure(Code.LIMIT)
        if lengths and int(lengths[0]) != len(body):
            raise SourceFailure(Code.FAILURE)
        stream.budget.check()
        return RawPage(items=(RawItem(bytes(body), url),), bytes_received=stream.received+len(url.encode('utf-8')),
                       peer_ip=peer)
    finally:
        response.close()
