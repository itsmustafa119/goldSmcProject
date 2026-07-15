import unittest

import pandas as pd

from gold_smc.config import project_path
from gold_smc.chart import format_compact_number
from gold_smc.indicators import calculate_structure_map


class GoldSmcTestCase(unittest.TestCase):
    def test_project_path_resolves_relative_path(self):
        relative_path = "test-file.txt"
        resolved = project_path(relative_path)

        self.assertTrue(resolved.is_absolute())
        self.assertTrue(str(resolved).endswith(relative_path))

    def test_format_compact_number(self):
        self.assertEqual(format_compact_number(123), "123")
        self.assertEqual(format_compact_number(1234), "1.23k")
        self.assertEqual(format_compact_number(1234567), "1.23M")
        self.assertEqual(format_compact_number(-1234567890), "-1.23B")

    def test_calculate_structure_map_returns_columns(self):
        data = pd.DataFrame(
            {
                "time": pd.date_range("2026-01-01", periods=4, freq="15min"),
                "open": [100.0, 101.0, 102.0, 101.0],
                "high": [101.0, 102.0, 103.0, 102.0],
                "low": [99.0, 100.0, 101.0, 100.0],
                "close": [100.5, 101.5, 102.5, 101.5],
                "volume": [100, 110, 120, 130],
            }
        )
        swings = pd.DataFrame(
            {
                "HighLow": [1, -1, 1, -1],
                "Level": [101.0, 100.0, 102.0, 100.0],
            },
            index=data.index,
        )

        structure = calculate_structure_map(data, swings)

        self.assertEqual(len(structure), len(data))
        self.assertIn("Support", structure.columns)
        self.assertIn("Resistance", structure.columns)
        self.assertIn("Zone", structure.columns)


if __name__ == "__main__":
    unittest.main()
