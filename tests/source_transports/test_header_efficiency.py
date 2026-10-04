"""Synthetic aggregate microbenchmark, not source or per-invocation telemetry."""
from time import monotonic, process_time

from radar.adapters.sources.transports import https as module
from conftest import transport_call


def test_bounded_large_header_acquisition(tls_server_factory,monkeypatch):
    # 28 distinct lines, ~25 KiB: near the cap, not a forbidden huge line.
    payload=(b'HTTP/1.1 200 Fixture\r\nContent-Length: 1\r\n'
             +(b'X-Synthetic: '+b'x'*887+b'\r\n')*28+b'\r\nz')
    original=module.GuardedStream._receive
    acquisitions=peak_buffer=0
    def observed(stream,maximum=4096):
        nonlocal acquisitions,peak_buffer
        acquisitions+=1
        result=original(stream,maximum)
        peak_buffer=max(peak_buffer,len(stream.buffer))
        return result
    monkeypatch.setattr(module.GuardedStream,'_receive',observed)
    wall_start,cpu_start=monotonic(),process_time()
    for _ in range(6):
        _,client=tls_server_factory(((payload,0),))
        page=client.read(transport_call(timeout=5),cancelled=lambda:False)
        assert page.items[0].content==b'z'
        assert page.bytes_received==len(payload)+len(page.items[0].url.encode())
        assert all(sock.fileno()==-1 for sock in client.tls_sockets)
    print(f'synthetic_headers runs=6 http_bytes={6*len(payload)} recv_calls={acquisitions} '
          f'peak_buffer={peak_buffer} wall_seconds={monotonic()-wall_start:.6f} '
          f'process_cpu_seconds={process_time()-cpu_start:.6f}')
    assert peak_buffer<=4096
    assert acquisitions<100  # Structural bound, not a flaky wall-time threshold.
