"""Sequential, fixture-only Docker measurements (not AWS billing).

Run ``python -m lab.runner --help``. Images are never built implicitly.
Only uniquely named containers created by this runner are removed.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
GIB = 1024 ** 3
TAGS = {s: f"market-radar-lab/{s}:local" for s in ("browser", "sessions")}
DOCKERFILES = {s: f"lab/{s}/Dockerfile" for s in TAGS}
CGROUP_SCRIPT = """const fs=require('fs');const out={};
for(const [key,file] of Object.entries({memory_peak_bytes:'memory.peak',memory_current_bytes:'memory.current',memory_events:'memory.events',cpu_stat:'cpu.stat',io_stat:'io.stat'})){
try{const s=fs.readFileSync('/sys/fs/cgroup/'+file,'utf8').trim();out[key]=key.endsWith('_bytes')?Number(s):key==='io_stat'?s:Object.fromEntries(s.split('\\n').map(l=>{const [k,v]=l.split(/\\s+/);return[k,Number(v)]}));}catch(e){out[key]=null;}}
console.log(JSON.stringify(out));"""
READY_SCRIPT = """const http=require('http');const until=Date.now()+15000;
function probe(){const r=http.get('http://127.0.0.1:8080/',res=>{res.resume();process.exit(0)});
r.on('error',()=>{if(Date.now()>until)process.exit(1);setTimeout(probe,100)});
r.setTimeout(500,()=>r.destroy());}probe();"""


@dataclass
class CommandResult:
    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False


class ResourcePressure(RuntimeError):
    """RAM admission rejection; eligible smaller profiles may still run."""


def command(args: list[str], timeout: float, *, input_text: str | None = None) -> CommandResult:
    """No shell, credentials, arbitrary executable, or unbounded wait."""
    if not args or args[0] != "docker":
        raise ValueError("Only the Docker executable is allowed")
    try:
        result = subprocess.run(args, input=input_text, capture_output=True, text=True,
                                timeout=timeout, check=False, cwd=ROOT,
                                encoding="utf-8", errors="replace")
        return CommandResult(result.returncode, result.stdout, result.stderr)
    except subprocess.TimeoutExpired as exc:
        def decode(value: Any) -> str:
            return value.decode("utf-8", "replace") if isinstance(value, bytes) else value or ""
        return CommandResult(None, decode(exc.stdout), decode(exc.stderr), True)
    except OSError as exc:
        return CommandResult(None, stderr=str(exc))


def checked(args: list[str], timeout: float = 20) -> str:
    result = command(args, timeout)
    if result.timed_out or result.returncode != 0:
        raise RuntimeError(f"Docker {args[1]} failed: {result.stderr[-2000:] or 'deadline/executable failure'}")
    return result.stdout


def available_memory() -> int:
    if os.name == "nt":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong) for name in
                ("total_phys", "avail_phys", "total_page", "avail_page", "total_virtual", "avail_virtual", "extended")]
        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise RuntimeError("Cannot determine available Windows RAM")
        return status.avail_phys
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        for line in meminfo.read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    raise RuntimeError("Available host RAM cannot be verified on this platform")


def parse_bytes(text: str) -> int:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(B|KiB|MiB|GiB|TiB|kB|KB|MB|GB|TB)\s*", text)
    if not match:
        raise ValueError("Unrecognized Docker memory measurement")
    unit = match[2]
    powers = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4,
              "kB": 1000, "KB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4}
    return int(Decimal(match[1]) * powers[unit])


def preflight(memory_mib: int, reserve_gib: float = 4) -> dict[str, Any]:
    if not math.isfinite(reserve_gib) or not 3 <= reserve_gib <= 16:
        raise ValueError("Host RAM reserve must be finite and between 3 and 16 GiB")
    info = json.loads(checked(["docker", "info", "--format", "{{json .}}"], 30))
    if info.get("OSType") != "linux":
        raise RuntimeError("Linux Docker Engine is required; no Docker/WSL settings will be changed")
    host_available = available_memory()
    headroom = math.ceil(memory_mib * 1024**2 * 1.25)
    reserve_bytes = math.ceil(reserve_gib * GIB)
    required = reserve_bytes + headroom
    if host_available < required:
        raise ResourcePressure(f"Host RAM pressure: {host_available / GIB:.2f} GiB available; {required / GIB:.2f} GiB required ({reserve_gib:g} GiB reserve + 25% headroom)")
    stats = checked(["docker", "stats", "--no-stream", "--format", "{{json .}}"], 30)
    used = sum(parse_bytes(json.loads(line)["MemUsage"].split("/")[0]) for line in stats.splitlines() if line.strip())
    engine_total = int(info.get("MemTotal", 0))
    if not engine_total:
        raise RuntimeError("Docker Engine did not report total RAM")
    if engine_total - used < headroom + 512 * 1024**2:
        raise ResourcePressure("Docker VM RAM pressure: need requested memory plus 25% headroom and 512 MiB VM reserve")
    free_disk = shutil.disk_usage(ROOT).free
    if free_disk < 2 * GIB:
        raise RuntimeError("Need at least 2 GiB free on the workspace disk")
    return {"host_available_bytes": host_available, "host_required_bytes": required,
            "host_reserve_gib": reserve_gib, "host_reserve_bytes": reserve_bytes,
            "memory_headroom_factor": 1.25, "reduced_reserve_experiment": reserve_gib < 4,
            "docker_total_bytes": engine_total, "docker_running_memory_bytes": used,
            "workspace_disk_free_bytes": free_disk,
            "disk_scope": "workspace filesystem; Docker VM disk availability not measured",
            "architecture": info.get("Architecture"), "docker_version": info.get("ServerVersion")}


def acquire_lab_lock(reports: Path):
    """An OS lock prevents two runner processes from overlapping containers.

    The handle owns the lock; close (including process exit) releases it. The
    persistent one-byte file is not an indication of a running benchmark.
    """
    handle = (reports / "runner.lock").open("a+b")
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b"1")
            handle.flush()
        return handle
    except OSError as exc:
        handle.close()
        raise RuntimeError("Another local benchmark runner holds the lab lock; no container started") from exc


def image_metadata(suite: str) -> dict[str, Any]:
    item = json.loads(checked(["docker", "image", "inspect", TAGS[suite]], 20))[0]
    return {"image_tag": TAGS[suite], "image_id": item["Id"],
            "image_digest": (item.get("RepoDigests") or [None])[0],
            "image_size_bytes": item["Size"], "image_architecture": item.get("Architecture"),
            "image_os": item.get("Os")}


def container_name(run_id: str) -> str:
    return f"market-radar-lab-{run_id}-{uuid.uuid4().hex[:12]}"


def validate_name(name: str) -> None:
    if not re.fullmatch(r"market-radar-lab-[0-9a-f]{12}-[0-9a-f]{12}", name):
        raise ValueError("Refusing to operate on a container outside this runner's namespace")


def run_args(name: str, suite: str, memory: int, cpus: float) -> list[str]:
    validate_name(name)
    if suite not in TAGS or not 64 <= memory <= 6144 or not math.isfinite(cpus) or not 0.1 <= cpus <= 4:
        raise ValueError("Invalid suite or resource limits")
    tmpfs = "/tmp:rw,exec,nosuid,nodev,size=768m" if suite == "browser" else "/tmp:rw,noexec,nosuid,nodev,size=32m"
    pids = "256" if suite == "browser" else "32"
    return ["docker", "run", "--detach", "--name", name,
            "--label", "market-radar.fixture-only=true", "--network", "none",
            "--read-only", "--tmpfs", tmpfs, "--pids-limit", pids, "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--memory", f"{memory}m",
            "--memory-swap", f"{memory}m", "--cpus", str(cpus), TAGS[suite]]


def snapshot(name: str) -> dict[str, Any]:
    validate_name(name)
    result = command(["docker", "exec", name, "node", "-e", CGROUP_SCRIPT], 5)
    if result.returncode != 0 or result.timed_out:
        return {"unavailable": True}
    try:
        value = json.loads(result.stdout)
        return value if isinstance(value, dict) else {"unavailable": True}
    except (ValueError, TypeError):
        return {"unavailable": True}


def state(name: str) -> dict[str, Any]:
    validate_name(name)
    result = command(["docker", "inspect", "--format", "{{json .State}}", name], 5)
    try:
        value = json.loads(result.stdout) if result.returncode == 0 else {}
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


def cleanup(name: str) -> dict[str, Any]:
    validate_name(name)
    started = time.monotonic()
    removed = False
    for _ in range(2):
        result = command(["docker", "rm", "--force", name], 15)
        if result.returncode == 0 or (not result.timed_out and "No such container" in result.stderr):
            removed = True
            break
    return {"cleanup_ms": (time.monotonic() - started) * 1000,
            "cleanup_status": "removed" if removed else "failed"}


def cost_fields(memory: int, seconds: float | None, useful: int, price: Decimal | None) -> dict[str, Any]:
    units = Decimal(memory) / Decimal(1024) * Decimal(str(seconds)) if seconds is not None else None
    estimated = units * price if price is not None and units is not None else None
    return {"approx_gb_seconds": float(units) if units is not None else None,
            "approx_gb_seconds_per_useful_record": float(units / useful) if useful > 0 and units is not None else None,
            "price_per_gb_second": str(price) if price is not None else None,
            "price_source": "user-input" if price is not None else None,
            "approx_compute_cost": str(estimated) if estimated is not None else None,
            "approx_compute_cost_per_useful_record": str(estimated / useful) if estimated is not None and useful > 0 else None,
            "cost_currency": None, "request_storage_network_cost": None,
            "cost_basis": "MiB/1024 × local invocation seconds; NOT AWS billing; excludes startup, cleanup, requests, storage and network"}


def browser_event(mode: str, batch: int, timeout: float) -> dict[str, Any]:
    if mode not in ("direct", "opencli") or not 1 <= batch <= 10:
        raise ValueError("Only bounded synthetic browser fixtures are supported")
    return {"schema_version": 1, "suite": "browser", "fixture_only": True,
            "mode": mode, "batch": batch, "repeats": 1, "session_mode": "full",
            "deadline_ms": min(60000, int(timeout * 1000))}


def decode_output(result: CommandResult) -> tuple[str, int, dict[str, Any] | None]:
    if result.timed_out:
        return "timeout", 0, None
    try:
        payload = json.loads(result.stdout)
        useful = payload.get("useful_records", 0)
        if not isinstance(payload, dict) or type(useful) is not int or useful < 0 or not isinstance(payload.get("status"), str):
            raise ValueError("Invalid fixture result")
        status = payload["status"]
        if result.returncode != 0:
            status = "failed"
        return status, useful if status in ("ok", "passed", "success") else 0, payload
    except (ValueError, TypeError, AttributeError):
        return "invalid_result" if result.returncode == 0 else "failed", 0, None


def cpu_seconds(before: dict[str, Any], after: dict[str, Any]) -> float | None:
    first = (before.get("cpu_stat") or {}).get("usage_usec")
    last = (after.get("cpu_stat") or {}).get("usage_usec")
    return max(0, last - first) / 1e6 if isinstance(first, (int, float)) and isinstance(last, (int, float)) else None


def benchmark_container(args: argparse.Namespace, memory: int, mode: str,
                        run_id: str, metadata: dict[str, Any], trial: int,
                        *, deadline: float | None = None) -> list[dict[str, Any]]:
    name = container_name(run_id)
    records: list[dict[str, Any]] = []
    started = time.monotonic()
    base = {"schema_version": 1, "run_id": run_id, "suite": args.suite, "mode": mode,
            "trial": trial, "fixture_only": True, "container_name": name,
            "memory_limit_mib": memory, "cpu_limit": args.cpus,
            "batch": args.batch if args.suite == "browser" else None,
            "timeout_seconds": args.timeout, **metadata}
    config = {"suite": args.suite, "mode": mode, "memory": memory, "cpus": args.cpus,
              "batch": base["batch"], "fixture_only": True, "timeout": args.timeout}
    base["config_hash"] = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    def bounded_timeout(limit: float) -> float:
        return max(0.001, min(limit, deadline - time.monotonic())) if deadline is not None else limit
    try:
        launch = command(run_args(name, args.suite, memory, args.cpus), bounded_timeout(30))
        ready = None
        if launch.returncode == 0 and args.suite == "browser":
            ready = command(["docker", "exec", name, "node", "-e", READY_SCRIPT], bounded_timeout(20))
        startup_ms = (time.monotonic() - started) * 1000
        if launch.returncode != 0 or launch.timed_out or (ready and (ready.returncode != 0 or ready.timed_out)):
            records.append({**base, "status": "startup_timeout" if launch.timed_out or (ready and ready.timed_out) else "startup_failed",
                            "cold_local": True, "invocation_index": 0, "startup_ms": startup_ms,
                            "execution_ms": None, "cpu_seconds": None, "peak_cgroup_bytes": None,
                            "useful_records": 0, "error": (launch.stderr or (ready.stderr if ready else ""))[-2000:],
                            **cost_fields(memory, None, 0, args.price)})
            return records
        repetitions = 1 + args.warm_invocations if args.suite == "browser" else 1
        for index in range(repetitions):
            if deadline is not None and time.monotonic() >= deadline:
                records.append({**base, "status": "run_budget_exhausted", "cold_local": index == 0,
                                "invocation_index": index, "startup_ms": startup_ms if index == 0 else 0,
                                "execution_ms": None, "cpu_seconds": None, "peak_cgroup_bytes": None,
                                "useful_records": 0, "abort_remaining_runs": True,
                                "abort_reason": "Overall run budget exhausted",
                                **cost_fields(memory, None, 0, args.price)})
                break
            before = snapshot(name) if args.suite == "browser" else {"cpu_stat": {"usage_usec": 0}}
            begin = time.monotonic()
            if args.suite == "browser":
                event = browser_event(mode, args.batch, args.timeout)
                result = command(["docker", "exec", "-i", name, "node", "/var/task/lab/browser/invoke.mjs"],
                                 bounded_timeout(args.timeout), input_text=json.dumps(event))
            else:
                waited = command(["docker", "wait", name], bounded_timeout(args.timeout))
                logs = command(["docker", "logs", name], 5)
                try:
                    exitcode = int(waited.stdout.strip()) if waited.returncode == 0 else waited.returncode
                except ValueError:
                    exitcode = None
                result = CommandResult(exitcode, logs.stdout, logs.stderr, waited.timed_out)
            elapsed = time.monotonic() - begin
            status, useful, payload = decode_output(result)
            handler_ms = (payload or {}).get("execution_ms", ((payload or {}).get("timings") or {}).get("execution_ms"))
            handler_timings = (payload or {}).get("timings") or {}
            handler_parts = [handler_timings.get(key) for key in ("init_ms", "execution_ms", "cleanup_ms")]
            handler_total_ms = sum(handler_parts) if all(type(part) in (int, float) and math.isfinite(part) and part >= 0 for part in handler_parts) else None
            measured_seconds = elapsed
            if args.suite == "sessions" and type(handler_ms) in (int, float) and math.isfinite(handler_ms) and handler_ms >= 0:
                measured_seconds = handler_ms / 1000
            after = snapshot(name) if args.suite == "browser" else (payload or {}).get("cgroup") or {"unavailable": True}
            if not isinstance(after, dict):
                after = {"unavailable": True}
            inspection = state(name)
            if inspection.get("OOMKilled") or (after.get("memory_events") or {}).get("oom_kill", 0) > 0:
                status, useful = "oom", 0
            record = {**base, "operation_id": uuid.uuid4().hex, "cold_local": index == 0,
                      "invocation_index": index, "startup_ms": startup_ms if index == 0 else 0,
                      "execution_ms": measured_seconds * 1000, "invoke_subprocess_ms": elapsed * 1000,
                      "execution_time_source": "session selftest monotonic timer" if measured_seconds != elapsed else "host invocation subprocess",
                      "handler_total_ms": handler_total_ms,
                      "handler_time_basis": "fixture init_ms + execution_ms + cleanup_ms; excludes host Docker exec and RIE envelope" if handler_total_ms is not None else None,
                      "cpu_seconds": cpu_seconds(before, after),
                      "peak_cgroup_bytes": after.get("memory_peak_bytes"),
                      "memory_current_bytes": after.get("memory_current_bytes"),
                      "cgroup_before": before, "cgroup_after": after,
                      "peak_scope": "container lifetime (includes prior warm invocations and metric probes)",
                      "container_state": inspection, "status": status, "useful_records": useful,
                      "runtime_result": payload, "stdout": result.stdout[-65536:], "stderr": result.stderr[-8192:],
                      **cost_fields(memory, measured_seconds, useful, args.price)}
            records.append(record)
            if result.timed_out or status == "oom" or (args.suite == "browser" and inspection.get("Running") is False):
                break
    finally:
        final_state = state(name)
        closing = cleanup(name)
        for index, record in enumerate(records):
            record.update(closing)
            record["cleanup_ms"] = closing["cleanup_ms"] if index == len(records) - 1 else 0
            record["cleanup_scope"] = "container cleanup recorded on final invocation only"
            record["final_container_state"] = final_state
            if final_state.get("OOMKilled"):
                record.update(status="oom", useful_records=0)
                record.update(cost_fields(memory, (record.get("execution_ms") or 0) / 1000, 0, args.price))
        if closing["cleanup_status"] != "removed":
            # A second container must never start while cleanup is uncertain.
            for record in records:
                record["abort_remaining_runs"] = True
    return records


def memory_list(value: str) -> list[int]:
    try:
        values = [int(item) for item in value.split(",")]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use comma-separated integer MiB values") from exc
    if not values or len(values) > 8 or len(set(values)) != len(values) or any(not 64 <= v <= 6144 for v in values):
        raise argparse.ArgumentTypeError("Use 1–8 distinct memory limits between 64 and 6144 MiB")
    return values


def positive_price(value: str) -> Decimal:
    try:
        result = Decimal(value)
        if not result.is_finite() or result < 0:
            raise ValueError()
        return result
    except (ValueError, ArithmeticError) as exc:
        raise argparse.ArgumentTypeError("Rate must be finite and non-negative") from exc


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    cli.add_argument("--suite", choices=tuple(TAGS), default="browser")
    cli.add_argument("--mode", choices=("direct", "opencli", "both"), default="both", help="Browser comparison; sessions use selftest")
    cli.add_argument("--memory-mib", type=memory_list, help="Comma-separated MiB; browser default 1024,1600,2048; sessions 128,256")
    cli.add_argument("--cpus", type=float, default=1)
    cli.add_argument("--host-reserve-gib", type=float, default=4,
                     help="Explicit host RAM reserve, 3–16 GiB (default 4); reductions are marked experiments")
    cli.add_argument("--trials", type=int, default=3, help="Fresh containers per profile")
    cli.add_argument("--warm-invocations", type=int, default=0, help="Additional browser invocations in each same container")
    cli.add_argument("--batch", type=int, default=3)
    cli.add_argument("--timeout", type=float, default=90, help="Invocation subprocess deadline in seconds")
    cli.add_argument("--run-timeout", type=float, default=1800, help="Overall run budget in seconds (build excluded); checked between containers")
    cli.add_argument("--build", action="store_true", help="Explicitly build the suite's exact Dockerfile; never concurrent with runs")
    cli.add_argument("--price-per-gb-second", dest="price", type=positive_price, help="Optional user-supplied rate; no AWS price inferred")
    return cli


def main(argv: list[str] | None = None) -> int:
    cli = parser()
    args = cli.parse_args(argv)
    if not math.isfinite(args.cpus) or not 0.1 <= args.cpus <= 4:
        cli.error("--cpus must be finite and between 0.1 and 4")
    if not math.isfinite(args.host_reserve_gib) or not 3 <= args.host_reserve_gib <= 16:
        cli.error("--host-reserve-gib must be finite and between 3 and 16 GiB")
    if not math.isfinite(args.timeout) or not 1 <= args.timeout <= 300:
        cli.error("--timeout must be between 1 and 300 seconds")
    if not math.isfinite(args.run_timeout) or not 1 <= args.run_timeout <= 7200:
        cli.error("--run-timeout must be between 1 and 7200 seconds")
    if not 1 <= args.trials <= 100 or not 0 <= args.warm_invocations <= 100 or not 1 <= args.batch <= 10:
        cli.error("trials: 1–100; warm invocations: 0–100; batch: 1–10")
    if args.suite == "sessions" and args.warm_invocations:
        cli.error("sessions selftest exits; warm process reuse applies only to browser")
    args.memory_mib = args.memory_mib or ([1024, 1600, 2048] if args.suite == "browser" else [128, 256])
    run_id = uuid.uuid4().hex[:12]
    reports = ROOT / ".local" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    report = reports / f"{run_id}.jsonl"
    failures = 0
    with report.open("x", encoding="utf-8") as output:
        def emit(record: dict[str, Any]) -> None:
            output.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
            output.flush()
        lock_handle = None
        try:
            lock_handle = acquire_lab_lock(reports)
            host = preflight(min(args.memory_mib), reserve_gib=args.host_reserve_gib)
            if args.build:
                checked(["docker", "build", "--file", DOCKERFILES[args.suite], "--tag", TAGS[args.suite], "."], 900)
                host = preflight(min(args.memory_mib), reserve_gib=args.host_reserve_gib)
            metadata = image_metadata(args.suite)
            if metadata["image_os"] != "linux":
                raise RuntimeError("Benchmark image must use Linux")
            native_arch = {"x86_64": "amd64", "aarch64": "arm64"}.get(host["architecture"], host["architecture"])
            if native_arch != metadata["image_architecture"]:
                raise RuntimeError("Image architecture differs from Docker host; emulated benchmarks are not supported")
            emit({"record_type": "run", "run_id": run_id, "fixture_only": True,
                  "timestamp": datetime.now(timezone.utc).isoformat(), "preflight": host,
                  "arguments": {**vars(args), "price": str(args.price) if args.price is not None else None},
                  "limitations": "Docker/RIE local stress test; not AWS CPU, IAM, networking or billing",
                  **metadata})
            modes = (["direct", "opencli"] if args.mode == "both" else [args.mode]) if args.suite == "browser" else ["selftest"]
            deadline = time.monotonic() + args.run_timeout
            for trial in range(1, args.trials + 1):
                # Alternate profile ordering to reduce drift bias.
                profiles = [(memory, mode) for memory in args.memory_mib for mode in modes]
                if trial % 2 == 0:
                    profiles.reverse()
                for memory, mode in profiles:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Overall run budget exhausted; no further container started")
                    try:
                        preflight(memory, reserve_gib=args.host_reserve_gib)
                    except ResourcePressure as exc:
                        emit({"record_type": "measurement", "schema_version": 1, "run_id": run_id,
                              "operation_id": uuid.uuid4().hex, "suite": args.suite, "mode": mode,
                              "trial": trial, "fixture_only": True, "status": "skipped_resource",
                              "reason": str(exc), "memory_limit_mib": memory, "cpu_limit": args.cpus,
                              "batch": args.batch if args.suite == "browser" else None,
                              "timeout_seconds": args.timeout, "cold_local": True,
                              "startup_ms": 0, "execution_ms": None, "cleanup_ms": 0,
                              "container_name": None, "cpu_seconds": None,
                              "peak_cgroup_bytes": None, "useful_records": 0, **metadata,
                              **cost_fields(memory, None, 0, args.price)})
                        failures += 1
                        print(f"Local benchmark skipped: {args.suite}/{mode}, {memory} MiB: {exc}", file=sys.stderr, flush=True)
                        continue
                    print(f"Local benchmark: {args.suite}/{mode}, {memory} MiB, {args.cpus} CPU, trial {trial}/{args.trials}", file=sys.stderr, flush=True)
                    records = benchmark_container(args, memory, mode, run_id, metadata, trial, deadline=deadline)
                    for record in records:
                        emit({"record_type": "measurement", **record})
                        failures += record["status"] not in ("ok", "passed", "success")
                    aborted = next((record for record in records if record.get("abort_remaining_runs")), None)
                    if aborted:
                        raise RuntimeError(aborted.get("abort_reason", "Scoped cleanup failed; remaining runs aborted"))
        except (RuntimeError, ValueError, OSError, KeyError) as exc:
            emit({"record_type": "runner_error", "run_id": run_id, "fixture_only": True,
                  "error": str(exc), "status": "failed"})
            print(f"Local benchmark failed: {exc}\nReport: {report}", file=sys.stderr)
            return 2
        finally:
            if lock_handle is not None:
                lock_handle.close()
    print(json.dumps({"run_id": run_id, "report": str(report), "failed_measurements": failures,
                      "cost_note": "local approximation, NOT AWS billing"}))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
