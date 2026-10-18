import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from equities.desktop_backend import launch_arguments
from equities.paths import credentials_path, runtime_root


class DesktopTests(unittest.TestCase):
    def test_custom_runtime_is_outside_bundle(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.dict("os.environ", {"EQUITIES_HOME": folder}),
        ):
            self.assertEqual(runtime_root(), Path(folder).resolve())
            self.assertEqual(credentials_path(), Path(folder).resolve() / ".secrets/alpaca.json")

    def test_frozen_default_uses_application_support(self):
        home = Path.home()
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("sys.frozen", True, create=True),
            patch("pathlib.Path.home", return_value=home),
        ):
            self.assertEqual(
                runtime_root(), Path.home() / "Library/Application Support/Freqtrade Bolsa"
            )

    def test_desktop_settings_preserve_paper_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            path.write_text(
                json.dumps(
                    {
                        "auto": True,
                        "strategy": "sma",
                        "symbols": ["AAPL", "MSFT"],
                        "budget": 500,
                        "port": 8081,
                    }
                )
            )
            args = launch_arguments(path)
            self.assertIn("--auto", args)
            self.assertIn("AAPL,MSFT", args)
            self.assertIn("500", args)

    def test_invalid_autostart_never_enables_trading(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            path.write_text(json.dumps({"auto": "false"}))
            with self.assertRaises(ValueError):
                launch_arguments(path)

    def test_missing_settings_defaults_to_manual(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertNotIn("--auto", launch_arguments(Path(folder) / "missing.json"))
