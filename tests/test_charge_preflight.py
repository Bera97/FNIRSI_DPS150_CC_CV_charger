import unittest
from dataclasses import replace

from charge_config import ChargeConfig
from charge_preflight import check_setup


class ChargePreflightTests(unittest.TestCase):
    def setUp(self):
        self.config = ChargeConfig(
            model="EXAMPLE_ONLY",
            chemistry="example",
            capacity_Ah=0.200,
            capacitance_F=None,
            max_current_A=0.200,
            current_method="c_rate",
            current_selection=0.5,
            charge_current_A=0.100,
            cv_voltage_V=3.0,
            termination_current_A=0.020,
            termination_hold_s=30.0,
            timeout_s=7200.0,
        )

    def test_example_matches_setup_limits(self):
        self.assertEqual(check_setup(self.config), [])

    def test_voltage_above_setup_ceiling_is_rejected(self):
        config = replace(self.config, cv_voltage_V=9.01)
        self.assertTrue(check_setup(config))

    def test_current_below_minimum_is_rejected(self):
        config = replace(self.config, charge_current_A=0.009)
        self.assertTrue(check_setup(config))

    def test_current_above_commissioning_ceiling_is_rejected(self):
        config = replace(self.config, charge_current_A=0.101)
        self.assertTrue(check_setup(config))

    def test_nonrepresentable_voltage_is_rejected(self):
        config = replace(self.config, cv_voltage_V=3.005)
        self.assertTrue(check_setup(config))

    def test_nonrepresentable_current_is_rejected(self):
        config = replace(self.config, charge_current_A=0.0505)
        self.assertTrue(check_setup(config))

    def test_boundary_values_are_accepted(self):
        config = replace(
            self.config,
            cv_voltage_V=9.00,
            charge_current_A=0.010,
            termination_current_A=0.005,
        )
        self.assertEqual(check_setup(config), [])

    def test_measurement_threshold_is_not_treated_as_setpoint(self):
        config = replace(
            self.config,
            termination_current_A=0.0205,
        )
        self.assertEqual(check_setup(config), [])


if __name__ == "__main__":
    unittest.main()