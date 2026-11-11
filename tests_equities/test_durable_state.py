import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from equities.durable_state import write_json


class DurableStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "campaign.json"
        write_json(self.path, {"baseline": 1000})

    def test_atomic_roundtrip_uses_utf8(self):
        write_json(self.path, {"message": "Protección activada"})
        self.assertEqual(
            json.loads(self.path.read_text(encoding="utf-8")), {"message": "Protección activada"}
        )
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_sync_failure_preserves_old_file_and_removes_temporary(self):
        with patch("equities.durable_state.os.fsync", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                write_json(self.path, {"baseline": 2000})
        self.assertEqual(json.loads(self.path.read_text()), {"baseline": 1000})
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_replace_failure_preserves_old_file(self):
        with patch("equities.durable_state.os.replace", side_effect=OSError("replace failure")):
            with self.assertRaises(OSError):
                write_json(self.path, {"baseline": 2000})
        self.assertEqual(json.loads(self.path.read_text()), {"baseline": 1000})

    def test_invalid_numeric_state_cannot_replace_valid_state(self):
        with self.assertRaises(ValueError):
            write_json(self.path, {"baseline": float("nan")})
        self.assertEqual(json.loads(self.path.read_text()), {"baseline": 1000})
