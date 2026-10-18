import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from equities.process_lock import acquire_process_lock


class ProcessLockTests(unittest.TestCase):
    def test_other_process_is_excluded_until_owner_closes(self):
        script = """
import sys
from pathlib import Path
from equities.process_lock import acquire_process_lock
try:
    handle = acquire_process_lock(Path(sys.argv[1]))
except BlockingIOError:
    sys.exit(17)
handle.close()
"""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "process.lock"
            command = [sys.executable, "-B", "-c", script, str(path)]
            with acquire_process_lock(path):
                blocked = subprocess.run(
                    command, check=False, capture_output=True, text=True, timeout=10
                )
                self.assertEqual(blocked.returncode, 17, blocked.stderr)
            released = subprocess.run(
                command, check=False, capture_output=True, text=True, timeout=10
            )
            self.assertEqual(released.returncode, 0, released.stderr)
