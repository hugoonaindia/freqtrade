"""Keep trading rules independent of adapters and application orchestration."""

import ast
import unittest
from pathlib import Path


class ArchitectureTests(unittest.TestCase):
    def test_domain_dependencies_stay_inside_domain(self):
        allowed = {
            "strategies", "guards", "history_validation", "execution_quality",
            "backtest_metrics", "temporal_policy",
        }
        root = Path(__file__).resolve().parents[1] / "equities"
        for module in sorted(allowed):
            tree = ast.parse((root / (module + ".py")).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.level:
                    with self.subTest(module=module, line=node.lineno):
                        self.assertEqual(node.level, 1)
                        self.assertIn(node.module, allowed)
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = ([node.module or ""] if isinstance(node, ast.ImportFrom)
                             else [alias.name for alias in node.names])
                    with self.subTest(module=module, line=node.lineno):
                        self.assertFalse(any(name.split(".")[0] in {
                            "equities", "urllib", "http", "sqlite3", "subprocess",
                        } for name in names), "Domain must not call external adapters")


if __name__ == "__main__":
    unittest.main()
