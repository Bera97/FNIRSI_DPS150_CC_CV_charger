import copy
import tempfile
import unittest
from pathlib import Path

import yaml

from charge_config import ConfigError, load_charge_config


BASE_CONFIG = {
    "cell": {
        "model": "EXAMPLE_ONLY",
        "chemistry": "example",
        "capacity_Ah": 0.200,
        "capacitance_F": None,
    },
    "charge": {
        "max_current_A": 0.200,
        "current_A": None,
        "current_fraction": None,
        "c_rate": 0.5,
        "cv_voltage_V": 3.0,
        "termination_current_A": 0.020,
        "termination_hold_s": 30,
        "timeout_s": 7200,
    },
}


class ChargeConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)
        self.path = Path(self.temp_directory.name) / "charge.yaml"
        self.config = copy.deepcopy(BASE_CONFIG)

    def load(self):
        self.path.write_text(
            yaml.safe_dump(self.config),
            encoding="utf-8",
        )
        return load_charge_config(self.path)

    def select_current(self, method, value):
        for name in ("current_A", "current_fraction", "c_rate"):
            self.config["charge"][name] = None

        self.config["charge"][method] = value

    def test_c_rate_resolves_current(self):
        result = self.load()

        self.assertEqual(result.current_method, "c_rate")
        self.assertAlmostEqual(result.charge_current_A, 0.100)

    def test_absolute_current(self):
        self.select_current("current_A", 0.080)
        result = self.load()

        self.assertEqual(result.current_method, "current_A")
        self.assertAlmostEqual(result.charge_current_A, 0.080)

    def test_fraction_of_maximum(self):
        self.select_current("current_fraction", 0.25)
        result = self.load()

        self.assertEqual(result.current_method, "current_fraction")
        self.assertAlmostEqual(result.charge_current_A, 0.050)

    def test_current_equal_to_maximum_is_allowed(self):
        self.select_current("current_A", 0.200)
        result = self.load()

        self.assertAlmostEqual(result.charge_current_A, 0.200)

    def test_capacitance_without_ah_capacity(self):
        self.config["cell"]["capacity_Ah"] = None
        self.config["cell"]["capacitance_F"] = 100.0
        self.select_current("current_A", 0.100)

        result = self.load()

        self.assertIsNone(result.capacity_Ah)
        self.assertAlmostEqual(result.capacitance_F, 100.0)

    def test_no_current_method_is_rejected(self):
        self.select_current("current_A", None)

        with self.assertRaisesRegex(ConfigError, "exactly one"):
            self.load()

    def test_multiple_current_methods_are_rejected(self):
        self.config["charge"]["current_A"] = 0.100

        with self.assertRaisesRegex(ConfigError, "exactly one"):
            self.load()

    def test_c_rate_requires_capacity(self):
        self.config["cell"]["capacity_Ah"] = None

        with self.assertRaisesRegex(ConfigError, "capacity_Ah is required"):
            self.load()

    def test_excess_current_is_rejected_for_all_methods(self):
        cases = {
            "current_A": 0.300,
            "current_fraction": 1.5,
            "c_rate": 1.5,
        }

        for method, value in cases.items():
            with self.subTest(method=method):
                self.select_current(method, value)

                with self.assertRaises(ConfigError):
                    self.load()

    def test_invalid_numeric_values_are_rejected(self):
        invalid_values = [
            0,
            -0.1,
            True,
            "0.1",
            float("nan"),
            float("inf"),
            float("-inf"),
        ]

        for value in invalid_values:
            with self.subTest(value=repr(value)):
                self.select_current("current_A", value)

                with self.assertRaises(ConfigError):
                    self.load()

    def test_termination_must_be_below_charge_current(self):
        for value in (0.100, 0.150):
            with self.subTest(termination_current_A=value):
                self.config["charge"]["termination_current_A"] = value

                with self.assertRaisesRegex(
                    ConfigError,
                    "below the resolved",
                ):
                    self.load()

    def test_hold_must_be_shorter_than_timeout(self):
        self.config["charge"]["termination_hold_s"] = 7200

        with self.assertRaisesRegex(ConfigError, "shorter than"):
            self.load()

    def test_missing_required_field_is_rejected(self):
        del self.config["charge"]["cv_voltage_V"]

        with self.assertRaisesRegex(ConfigError, "missing keys"):
            self.load()

    def test_null_required_value_is_rejected(self):
        self.config["charge"]["cv_voltage_V"] = None

        with self.assertRaises(ConfigError):
            self.load()

    def test_unknown_key_is_rejected(self):
        self.config["charge"]["cv_volatge_V"] = 3.0

        with self.assertRaisesRegex(ConfigError, "unknown keys"):
            self.load()

    def test_duplicate_yaml_key_is_rejected(self):
        self.path.write_text(
            "cell: {}\n"
            "cell: {}\n"
            "charge: {}\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ConfigError, "Duplicate YAML key"):
            load_charge_config(self.path)

    def test_empty_yaml_is_rejected(self):
        self.path.write_text("", encoding="utf-8")

        with self.assertRaises(ConfigError):
            load_charge_config(self.path)

    def test_malformed_yaml_is_rejected(self):
        self.path.write_text(
            "cell: [unterminated\n",
            encoding="utf-8",
        )

        with self.assertRaises(yaml.YAMLError):
            load_charge_config(self.path)

    def test_blank_model_is_rejected(self):
        self.config["cell"]["model"] = "   "

        with self.assertRaises(ConfigError):
            self.load()


if __name__ == "__main__":
    unittest.main()