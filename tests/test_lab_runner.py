"""Runner control-plane tests; no Docker engine, social accounts, or network."""
import argparse
import contextlib
from decimal import Decimal
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from lab import runner


class RunnerTests(unittest.TestCase):
    def options(self, **kwargs):
        options = runner.parser().parse_args([])
        for key, value in kwargs.items():
            setattr(options, key, value)
        return options

    def test_command_has_no_shell_and_timeout_preserves_output(self):
        with patch.object(runner.subprocess, "run", side_effect=subprocess.TimeoutExpired("docker", 1, output=b"partial", stderr=b"deadline")) as execute:
            result = runner.command(["docker", "info"], 1)
        self.assertTrue(result.timed_out)
        self.assertEqual(result.stdout, "partial")
        self.assertEqual(result.stderr, "deadline")
        self.assertNotIn("shell", execute.call_args.kwargs)
        self.assertEqual(execute.call_args.kwargs["timeout"], 1)
        with self.assertRaises(ValueError):
            runner.command(["powershell", "malicious"], 1)

    def test_only_scoped_names_and_fixed_images(self):
        name = runner.container_name("a" * 12)
        args = runner.run_args(name, "browser", 1600, 1)
        self.assertEqual(args[-1], "market-radar-lab/browser:local")
        for pair in (("--network", "none"), ("--cap-drop", "ALL"), ("--memory", "1600m"), ("--memory-swap", "1600m"), ("--security-opt", "no-new-privileges")):
            self.assertEqual(args[args.index(pair[0]) + 1], pair[1])
        self.assertIn("--read-only", args)
        self.assertEqual(args[args.index("--tmpfs") + 1], "/tmp:rw,exec,nosuid,nodev,size=768m")
        self.assertEqual(args[args.index("--pids-limit") + 1], "256")
        session_args = runner.run_args(name, "sessions", 128, 1)
        self.assertEqual(session_args[session_args.index("--tmpfs") + 1], "/tmp:rw,noexec,nosuid,nodev,size=32m")
        self.assertEqual(session_args[session_args.index("--pids-limit") + 1], "32")
        for malicious in ("other-container", "--all", name + "; whoami", "$(whoami)", ""):
            with self.subTest(name=malicious), self.assertRaises(ValueError):
                runner.cleanup(malicious)
        for suite, memory, cpu in (("browser;whoami", 1600, 1), ("browser", 0, 1), ("browser", 1600, float("nan")), ("browser", 1600, 0)):
            with self.assertRaises(ValueError):
                runner.run_args(name, suite, memory, cpu)

    def test_event_is_bounded_fixture_only_without_credentials(self):
        event = runner.browser_event("opencli", 3, 90)
        self.assertEqual(event, {"schema_version": 1, "suite": "browser", "fixture_only": True,
                                "mode": "opencli", "batch": 3, "repeats": 1,
                                "session_mode": "full", "deadline_ms": 60000})
        with self.assertRaises(ValueError):
            runner.browser_event("$(curl)", 3, 90)
        with self.assertRaises(ValueError):
            runner.browser_event("direct", 11, 90)

    def test_default_host_timeout_reserves_cleanup_beyond_handler_deadline(self):
        timeout = self.options().timeout
        self.assertEqual(timeout, 90)
        self.assertEqual(runner.browser_event("direct", 3, timeout)["deadline_ms"], 60000)
        self.assertEqual(runner.browser_event("direct", 3, 10)["deadline_ms"], 10000)

    def test_resource_and_price_input_validation(self):
        self.assertEqual(runner.memory_list("1024,1600,2048"), [1024, 1600, 2048])
        for value in ("1024;whoami", "0", "1,2", "1024,1024", "99999"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                runner.memory_list(value)
        for value in ("nan", "Infinity", "-1", "bad"):
            with self.assertRaises(argparse.ArgumentTypeError):
                runner.positive_price(value)
        self.assertEqual(runner.positive_price("0"), Decimal(0))

    def test_cost_unknown_and_zero_success_have_no_fictional_unit_cost(self):
        unknown = runner.cost_fields(1024, 2, 0, None)
        self.assertEqual(unknown["approx_gb_seconds"], 2)
        self.assertIsNone(unknown["approx_gb_seconds_per_useful_record"])
        self.assertIsNone(unknown["approx_compute_cost"])
        priced = runner.cost_fields(1600, 2, 0, Decimal("0.0000166667"))
        self.assertEqual(priced["approx_gb_seconds"], 3.125)
        self.assertIsNone(priced["approx_compute_cost_per_useful_record"])
        self.assertEqual(priced["price_source"], "user-input")
        self.assertIn("NOT AWS billing", priced["cost_basis"])
        self.assertIsNone(runner.cost_fields(1024, None, 0, Decimal("0.1"))["approx_compute_cost"])

    def test_decode_failure_cannot_count_useful_records(self):
        result = runner.CommandResult(1, '{"status":"ok","useful_records":10}')
        self.assertEqual(runner.decode_output(result)[:2], ("failed", 0))
        result = runner.CommandResult(0, '{"status":"failed","useful_records":10}')
        self.assertEqual(runner.decode_output(result)[:2], ("failed", 0))
        for text in ("not json", "[]", '{"status":"ok","useful_records":true}', '{"status":"ok","useful_records":-1}'):
            self.assertEqual(runner.decode_output(runner.CommandResult(0, text))[:2], ("invalid_result", 0))

    def test_host_pressure_stops_before_any_container(self):
        info = json.dumps({"OSType": "linux", "MemTotal": 16 * runner.GIB})
        with patch.object(runner, "checked", return_value=info) as calls, patch.object(runner, "available_memory", return_value=5 * runner.GIB):
            with self.assertRaisesRegex(RuntimeError, "Host RAM pressure"):
                runner.preflight(2048)
        self.assertEqual(calls.call_count, 1)

    def test_engine_pressure_and_workspace_disk_are_verified(self):
        info = json.dumps({"OSType": "linux", "MemTotal": 4 * runner.GIB})
        stat = json.dumps({"MemUsage": "3GiB / 4GiB"})
        with patch.object(runner, "checked", side_effect=[info, stat]), patch.object(runner, "available_memory", return_value=10 * runner.GIB):
            with self.assertRaisesRegex(RuntimeError, "VM RAM pressure"):
                runner.preflight(1600)
        with patch.object(runner, "checked", side_effect=[info, ""]), patch.object(runner, "available_memory", return_value=10 * runner.GIB), patch.object(runner.shutil, "disk_usage", return_value=type("Disk", (), {"free": runner.GIB})()):
            with self.assertRaisesRegex(RuntimeError, "workspace disk"):
                runner.preflight(128)

    def test_non_linux_engine_is_rejected(self):
        with patch.object(runner, "checked", return_value='{"OSType":"windows"}'):
            with self.assertRaisesRegex(RuntimeError, "Linux Docker Engine"):
                runner.preflight(128)

    def test_os_lock_prevents_two_runner_processes_and_releases_on_close(self):
        with tempfile.TemporaryDirectory() as directory:
            first = runner.acquire_lab_lock(Path(directory))
            try:
                with self.assertRaisesRegex(RuntimeError, "lab lock"):
                    runner.acquire_lab_lock(Path(directory))
            finally:
                first.close()
            second = runner.acquire_lab_lock(Path(directory))
            second.close()

    def test_expired_overall_budget_never_invokes_fixture_and_still_cleans_up(self):
        with patch.object(runner, "command", return_value=runner.CommandResult(0)), patch.object(runner, "snapshot") as snapshot, patch.object(runner, "state", return_value={}), patch.object(runner, "cleanup", return_value={"cleanup_ms": 1, "cleanup_status": "removed"}) as cleaned:
            records = runner.benchmark_container(self.options(), 1600, "direct", "a" * 12, {}, 1, deadline=0)
        self.assertEqual(records[0]["status"], "run_budget_exhausted")
        self.assertTrue(records[0]["abort_remaining_runs"])
        snapshot.assert_not_called()
        cleaned.assert_called_once()

    def run_mock_browser(self, invoke, *, warm=2, cleanup_ok=True, oom=False):
        calls = []
        def fake(args, timeout, **kwargs):
            calls.append((args, timeout, kwargs))
            if args[1] == "exec" and "-i" in args:
                return invoke
            if args[1] == "rm" and not cleanup_ok:
                return runner.CommandResult(1, stderr="daemon error")
            return runner.CommandResult(0)
        metrics = {"memory_peak_bytes": 100, "memory_current_bytes": 50,
                   "memory_events": {"oom_kill": 0}, "cpu_stat": {"usage_usec": 100}}
        with patch.object(runner, "command", side_effect=fake), patch.object(runner, "snapshot", return_value=metrics), patch.object(runner, "state", return_value={"Running": not oom, "OOMKilled": oom}):
            records = runner.benchmark_container(self.options(warm_invocations=warm), 1600, "direct", "a" * 12, {}, 1)
        return records, calls

    def test_invocation_timeout_collects_state_and_always_removes_own_container(self):
        records, calls = self.run_mock_browser(runner.CommandResult(None, "partial", timed_out=True))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["status"], "timeout")
        self.assertEqual(records[0]["useful_records"], 0)
        self.assertEqual(records[0]["cleanup_status"], "removed")
        self.assertEqual(calls[-1][0][:3], ["docker", "rm", "--force"])
        self.assertEqual(calls[-1][0][3], records[0]["container_name"])
        self.assertEqual(sum("-i" in call[0] for call in calls), 1)

    def test_warm_reuses_one_container_and_records_startup_cleanup_once(self):
        records, calls = self.run_mock_browser(runner.CommandResult(0, '{"status":"ok","useful_records":3}'))
        self.assertEqual(len(records), 3)
        self.assertEqual([r["cold_local"] for r in records], [True, False, False])
        self.assertEqual(len({r["container_name"] for r in records}), 1)
        self.assertEqual(sum(call[0][1] == "run" for call in calls), 1)
        self.assertEqual(sum(call[0][1] == "rm" for call in calls), 1)
        self.assertEqual([r["startup_ms"] for r in records[1:]], [0, 0])
        self.assertEqual([r["cleanup_ms"] for r in records[:-1]], [0, 0])
        event = json.loads(next(call[2]["input_text"] for call in calls if "-i" in call[0]))
        self.assertTrue(event["fixture_only"])

    def test_oom_cannot_be_success_and_cleanup_failure_aborts_remaining_runs(self):
        records, _ = self.run_mock_browser(runner.CommandResult(0, '{"status":"ok","useful_records":3}'), oom=True)
        self.assertEqual(records[0]["status"], "oom")
        self.assertEqual(records[0]["useful_records"], 0)
        self.assertIsNone(records[0]["approx_gb_seconds_per_useful_record"])
        records, calls = self.run_mock_browser(runner.CommandResult(0, '{"status":"ok","useful_records":3}'), warm=0, cleanup_ok=False)
        self.assertTrue(records[0]["abort_remaining_runs"])
        self.assertEqual(sum(call[0][1] == "rm" for call in calls), 2)

    def test_startup_failure_always_cleans_up_with_unknown_execution_cost(self):
        with patch.object(runner, "command", return_value=runner.CommandResult(None, timed_out=True)), patch.object(runner, "state", return_value={}), patch.object(runner, "cleanup", return_value={"cleanup_ms": 1, "cleanup_status": "removed"}) as cleaned:
            records = runner.benchmark_container(self.options(), 1600, "direct", "a" * 12, {}, 1)
        cleaned.assert_called_once()
        self.assertEqual(records[0]["status"], "startup_timeout")
        self.assertIsNone(records[0]["execution_ms"])
        self.assertIsNone(records[0]["approx_gb_seconds"])

    def test_session_selftest_uses_default_cmd_and_reported_timing_metrics(self):
        calls = []
        payload = {"status": "ok", "useful_records": 7, "timings": {"execution_ms": 25},
                   "cgroup": {"memory_peak_bytes": 5000, "cpu_stat": {"usage_usec": 20000}}}
        def fake(args, timeout, **kwargs):
            calls.append(args)
            if args[1] == "wait":
                return runner.CommandResult(0, "0\n")
            if args[1] == "logs":
                return runner.CommandResult(0, json.dumps(payload))
            return runner.CommandResult(0)
        with patch.object(runner, "command", side_effect=fake), patch.object(runner, "state", return_value={"OOMKilled": False}):
            records = runner.benchmark_container(self.options(suite="sessions"), 128, "selftest", "a" * 12, {}, 1)
        self.assertEqual(records[0]["cpu_seconds"], 0.02)
        self.assertEqual(records[0]["execution_ms"], 25)
        self.assertEqual(records[0]["peak_cgroup_bytes"], 5000)
        self.assertEqual(records[0]["useful_records"], 7)
        self.assertFalse(any(command[1] == "exec" for command in calls))
        run = next(command for command in calls if command[1] == "run")
        self.assertEqual(run[-1], runner.TAGS["sessions"])

    def test_main_never_implicitly_builds_and_stops_after_cleanup_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(runner, "ROOT", Path(directory)), patch.object(runner, "preflight", return_value={"architecture": "x86_64"}), patch.object(runner, "image_metadata", return_value={"image_os": "linux", "image_architecture": "amd64"}), patch.object(runner, "checked") as checked, patch.object(runner, "benchmark_container", return_value=[{"status": "failed", "abort_remaining_runs": True}]) as benchmark, contextlib.redirect_stderr(io.StringIO()):
                exitcode = runner.main(["--suite", "sessions", "--trials", "3"])
            self.assertEqual(exitcode, 2)
            benchmark.assert_called_once()
            checked.assert_not_called()
            reports = list((Path(directory) / ".local" / "reports").glob("*.jsonl"))
            records = [json.loads(line) for line in reports[0].read_text().splitlines()]
            self.assertEqual(records[-1]["record_type"], "runner_error")

    def test_grid_runs_admitted_subset_and_counts_unavailable_profiles_as_nonpassed(self):
        inspected = []
        def preflight(memory, reserve_gib=4):
            inspected.append(memory)
            self.assertEqual(reserve_gib, 4)
            if memory == 2048:
                raise runner.ResourcePressure("Host RAM pressure: reserve unchanged")
            return {"architecture": "x86_64"}
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(runner, "ROOT", Path(directory)), patch.object(runner, "preflight", side_effect=preflight), patch.object(runner, "image_metadata", return_value={"image_os": "linux", "image_architecture": "amd64"}), patch.object(runner, "benchmark_container", return_value=[{"status": "ok", "useful_records": 1}]) as benchmark, contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()) as stdout:
                exitcode = runner.main(["--suite", "sessions", "--memory-mib", "128,2048", "--trials", "1"])
            self.assertEqual(exitcode, 1)
            self.assertEqual(inspected, [128, 128, 2048])
            benchmark.assert_called_once()
            self.assertEqual(benchmark.call_args.args[1], 128)
            summary = json.loads(stdout.getvalue())
            self.assertEqual(summary["failed_measurements"], 1)
            records = [json.loads(line) for line in Path(summary["report"]).read_text().splitlines()]
            self.assertEqual(records[-1]["status"], "skipped_resource")
            self.assertEqual(records[-1]["useful_records"], 0)
            self.assertIn("reserve unchanged", records[-1]["reason"])
            self.assertIsNone(records[-1]["approx_compute_cost"])
            self.assertIsNone(records[-1]["approx_gb_seconds_per_useful_record"])
            self.assertIsNone(records[-1]["container_name"])

    def test_non_resource_preflight_failure_aborts_grid(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(runner, "ROOT", Path(directory)), patch.object(runner, "preflight", side_effect=[{"architecture": "x86_64"}, RuntimeError("Docker Engine unavailable")]), patch.object(runner, "image_metadata", return_value={"image_os": "linux", "image_architecture": "amd64"}), patch.object(runner, "benchmark_container") as benchmark, contextlib.redirect_stderr(io.StringIO()):
                exitcode = runner.main(["--suite", "sessions", "--memory-mib", "128,256", "--trials", "1"])
            self.assertEqual(exitcode, 2)
            benchmark.assert_not_called()

    def test_handler_timing_is_separate_from_host_invocation_time(self):
        payload = {"status": "ok", "useful_records": 3,
                   "timings": {"init_ms": 10, "execution_ms": 20, "cleanup_ms": 5}}
        records, _ = self.run_mock_browser(runner.CommandResult(0, json.dumps(payload)), warm=0)
        self.assertEqual(records[0]["handler_total_ms"], 35)
        self.assertEqual(records[0]["execution_time_source"], "host invocation subprocess")
        self.assertIn("excludes host Docker exec", records[0]["handler_time_basis"])

    def test_host_reserve_defaults_to_four_and_manual_three_is_explicit(self):
        self.assertEqual(self.options().host_reserve_gib, 4)
        info = json.dumps({"OSType": "linux", "MemTotal": 16 * runner.GIB})
        available = int(3.79 * runner.GIB)
        with patch.object(runner, "checked", return_value=info), patch.object(runner, "available_memory", return_value=available):
            with self.assertRaisesRegex(runner.ResourcePressure, "4 GiB reserve"):
                runner.preflight(480)
        with patch.object(runner, "checked", side_effect=[info, ""]), patch.object(runner, "available_memory", return_value=available), patch.object(runner.shutil, "disk_usage", return_value=type("Disk", (), {"free": 5 * runner.GIB})()):
            result = runner.preflight(480, reserve_gib=3)
        self.assertEqual(result["host_reserve_gib"], 3)
        self.assertEqual(result["host_reserve_bytes"], 3 * runner.GIB)
        self.assertEqual(result["host_required_bytes"], 3 * runner.GIB + 480 * 1024**2 * 1.25)
        self.assertEqual(result["memory_headroom_factor"], 1.25)
        self.assertTrue(result["reduced_reserve_experiment"])

    def test_invalid_reserve_rejected_before_docker_and_cli_rejects_nonfinite(self):
        for value in (2.99, 17, float("nan"), float("inf")):
            with self.subTest(value=value), patch.object(runner, "checked") as checked:
                with self.assertRaises(ValueError):
                    runner.preflight(480, reserve_gib=value)
                checked.assert_not_called()
        for value in ("2.99", "17", "nan", "inf"):
            with self.subTest(value=value), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    runner.main(["--host-reserve-gib", value])
                self.assertEqual(error.exception.code, 2)

    def test_main_passes_manual_reserve_to_every_admission_check_and_report(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(runner, "ROOT", Path(directory)), patch.object(runner, "preflight", return_value={"architecture": "x86_64", "host_reserve_gib": 3, "reduced_reserve_experiment": True}) as preflight, patch.object(runner, "image_metadata", return_value={"image_os": "linux", "image_architecture": "amd64"}), patch.object(runner, "benchmark_container", return_value=[{"status": "ok"}]), contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()) as stdout:
                exitcode = runner.main(["--suite", "sessions", "--memory-mib", "480", "--trials", "1", "--host-reserve-gib", "3"])
            self.assertEqual(exitcode, 0)
            self.assertEqual(preflight.call_count, 2)
            self.assertTrue(all(call.kwargs["reserve_gib"] == 3 for call in preflight.call_args_list))
            records = [json.loads(line) for line in Path(json.loads(stdout.getvalue())["report"]).read_text().splitlines()]
            self.assertEqual(records[0]["arguments"]["host_reserve_gib"], 3)
            self.assertTrue(records[0]["preflight"]["reduced_reserve_experiment"])


if __name__ == "__main__":
    unittest.main()
