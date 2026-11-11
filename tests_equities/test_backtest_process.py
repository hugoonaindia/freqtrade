import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from equities.backtest_jobs import BacktestJobs


def successful_worker(symbols, config, holdout, analytics_path, result_path, messages):
    Path(result_path).write_text(
        json.dumps({"worker_pid": os.getpid(), "symbols": symbols}), encoding="utf-8"
    )
    messages.put({"state": "complete", "message": "done"})


def crashed_worker(*args):
    os._exit(17)


def waiting_worker(*args):
    time.sleep(30)


class IsolatedBacktestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root_patch = patch("equities.backtest_jobs.runtime_root", return_value=self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def job(self, target, timeout=10):
        job = BacktestJobs(isolated=True, process_target=target, timeout_seconds=timeout)
        self.addCleanup(job.close)
        return job

    def start(self, job):
        return job.start({"symbols": ["AAPL"], "start": "2024-01-01", "end": "2024-12-31"})

    def test_real_spawn_uses_other_pid_and_removes_temporary_files(self):
        job = self.job(successful_worker)
        started = self.start(job)
        job.wait(15)
        self.assertEqual(job.status()["state"], "complete", job.status())
        self.assertNotEqual(job.result(started["id"])["worker_pid"], os.getpid())
        self.assertIsNone(job.process)
        self.assertEqual(list((self.root / ".equities-state" / "backtest-tmp").iterdir()), [])

    def test_abrupt_worker_failure_releases_job(self):
        job = self.job(crashed_worker)
        self.start(job)
        job.wait(10)
        self.assertEqual(job.status()["state"], "error")
        self.assertIsNone(job.process)

    def test_timeout_terminates_only_the_research_worker(self):
        job = self.job(waiting_worker, 0.5)
        self.start(job)
        job.wait(10)
        self.assertEqual(job.status()["state"], "error")
        self.assertIn("tiempo máximo", job.status()["message"])
        self.assertIsNone(job.process)
        self.assertEqual(list((self.root / ".equities-state" / "backtest-tmp").iterdir()), [])

    def test_close_cancels_worker(self):
        job = self.job(waiting_worker)
        self.start(job)
        job.close()
        self.assertEqual(job.status()["state"], "error")
        self.assertIsNone(job.process)

    def test_linux_limits_are_applied_to_worker_not_parent(self):
        import types
        from unittest.mock import Mock
        from equities.backtest_process import apply_limits

        resource = types.SimpleNamespace(RLIMIT_AS=1, RLIMIT_CPU=2, setrlimit=Mock())
        with (
            patch.dict("sys.modules", {"resource": resource}),
            patch("equities.backtest_process.sys.platform", "linux"),
            patch("equities.backtest_process.os.nice", create=True) as nice,
            patch.dict("os.environ", {"EQUITIES_BACKTEST_MEMORY_MB": "384"}),
        ):
            apply_limits()
        resource.setrlimit.assert_any_call(1, (384 * 1024 * 1024, 384 * 1024 * 1024))
        resource.setrlimit.assert_any_call(2, (600, 600))
        nice.assert_called_once_with(10)

    def test_low_disk_blocks_research_before_spawning(self):
        from types import SimpleNamespace

        job = self.job(successful_worker)
        with patch(
            "equities.backtest_jobs.shutil.disk_usage", return_value=SimpleNamespace(free=1)
        ):
            self.start(job)
            job.wait(10)
        self.assertEqual(job.status()["state"], "error")
        self.assertIn("Espacio insuficiente", job.status()["message"])
        self.assertIsNone(job.process)
