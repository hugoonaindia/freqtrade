"""Writable runtime paths, independent of the signed macOS bundle."""

import os
import sys
from pathlib import Path


def runtime_root():
    configured = os.environ.get("EQUITIES_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path.home() / "Library/Application Support/Freqtrade Bolsa"
    return Path(__file__).resolve().parent.parent


def credentials_path():
    return runtime_root() / ".secrets/alpaca.json"
