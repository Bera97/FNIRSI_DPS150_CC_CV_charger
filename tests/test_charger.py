import asyncio
import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import charger
from charge_config import ChargeConfig, ConfigError
from charge_monitor import MonitorResult, Status
from dps150_adapter import FreshState


def example_config():
    return ChargeConfig(
        model="TEST_DUT",
        chemistry="test",
        capacity_Ah=0.200,
        capacitance_F=None,
        max_current_A=0.200,
        current_method="current_A",
        current_selection=0.100,
        charge_current_A=0.100,
        cv_voltage_V=3.00,
        termination_current_A=0.020,
        termination_hold_s=5.0,
        timeout_s=60.0,
    )


class FakeDevice:
    """Mock instrument with stateful output switching."""

    def __init__(self):
        self.enabled = False
        self.read_count = 0
        self.active_sample_override = {}
        self.active_error = None
        self.preparation_mismatch = False

        reader_task = MagicMock()
        reader_task.done.return_value = False

        self.transport = MagicMock()
        self.transport.is_connected = True
        self.transport.reader_task = reader_task

        self.connect = AsyncMock()
        self.disconnect = AsyncMock()
        self.set_current = AsyncMock()
        self.set_voltage = AsyncMock()

        self.enable_output = AsyncMock(side_effect=self._enable)
        self.disable_output = AsyncMock(side_effect=self._disable)
        self.read_fresh = AsyncMock(side_effect=self._read)

    async def _enable(self):
        self.enabled = True

    async def _disable(self):
        self.enabled = False

    async def _read(self, timeout_s=3.0):
        self.read_count += 1

        if self.enabled and self.active_error is not None:
            error = self.active_error
            self.active_error = None
            raise error

        sample = FreshState(
            received_at_s=asyncio.get_running_loop().time(),
            input_voltage_V=12.0,
            set_voltage_V=3.00,
            set_current_A=0.100,
            output_voltage_V=3.00 if self.enabled else 0.0,
            output_current_A=0.010 if self.enabled else 0.0,
            output_power_W=0.030 if self.enabled else 0.0,
            device_temperature_C=25.0,
            output_enabled=self.enabled,
            mode="CV",
            protection="NORMAL",
            protection_code=0,
        )

        # Read 1: initial output-off state.
        # Read 2: setpoint verification.
        if self.preparation_mismatch and self.read_count == 2:
            sample = replace(sample, set_voltage_V=2.0)

        if self.enabled and self.active_sample_override:
            sample = replace(sample, **self.active_sample_override)

        return sample


class ChargerRunnerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)

        self.run_directory = Path(self.temp_directory.name)
        self.config = example_config()
        self.device = FakeDevice()

        self.device_factory = self.enterContext(
            patch("charger.FreshDPS150", return_value=self.device)
        )

        # Skip countdown/poll/retry delays.
        # Tests still use the real asyncio wait_for implementation.
        self.enterContext(
            patch("charger.asyncio.sleep", new_callable=AsyncMock)
        )

        self.monitor_class = self.enterContext(
            patch("charger.ChargeMonitor")
        )
        self.monitor = self.monitor_class.return_value
        self.monitor.update.return_value = MonitorResult(
            status=Status.COMPLETE,
            reason="Simulated qualified termination.",
            termination_elapsed_s=5.0,
        )

        self.output = io.StringIO()
        self.enterContext(redirect_stdout(self.output))

    async def run_runner(self):
        return await charger.run_charge(
            self.config,
            "COM_TEST",
            self.run_directory,
        )

    def read_summary(self):
        return json.loads(
            (self.run_directory / "result.json").read_text(
                encoding="utf-8"
            )
        )

    def read_rows(self):
        with (self.run_directory / "measurements.csv").open(
            newline="",
            encoding="utf-8",
        ) as stream:
            return list(csv.DictReader(stream))

    async def test_completion_logs_and_switches_output_off(self):
        exit_code = await self.run_runner()
        summary = self.read_summary()
        rows = self.read_rows()

        self.assertEqual(exit_code, 0)
        self.assertEqual(summary["outcome"], "complete")
        self.assertTrue(summary["output_off_verified"])
        self.assertFalse(self.device.enabled)

        self.device.set_voltage.assert_awaited_once_with(3.00)
        self.device.set_current.assert_awaited_once_with(0.100)
        self.device.enable_output.assert_awaited_once()
        self.device.disconnect.assert_awaited_once()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "complete")
        self.assertEqual(rows[0]["supply_cv_qualified"], "True")
        self.assertEqual(summary["voltage_measurement"], "supply_side")

    async def test_monitor_fault_is_not_completion(self):
        self.monitor.update.return_value = MonitorResult(
            status=Status.FAULT,
            reason="Charge timeout reached.",
            termination_elapsed_s=0.0,
        )

        exit_code = await self.run_runner()
        summary = self.read_summary()

        self.assertEqual(exit_code, 1)
        self.assertEqual(summary["outcome"], "fault")
        self.assertIn("timeout", summary["reason"].lower())
        self.assertTrue(summary["output_off_verified"])
        self.assertFalse(self.device.enabled)
        self.assertEqual(self.read_rows()[0]["status"], "fault")

    async def test_active_read_timeout_triggers_shutdown(self):
        self.device.active_error = asyncio.TimeoutError(
            "Simulated telemetry timeout."
        )

        exit_code = await self.run_runner()
        summary = self.read_summary()

        self.assertEqual(exit_code, 1)
        self.assertEqual(summary["outcome"], "fault")
        self.assertIn("TimeoutError", summary["reason"])
        self.assertFalse(self.device.enabled)
        self.device.disconnect.assert_awaited_once()

    async def test_cancellation_runs_cleanup(self):
        self.device.active_error = asyncio.CancelledError()

        exit_code = await self.run_runner()
        summary = self.read_summary()

        self.assertEqual(exit_code, 130)
        self.assertEqual(summary["outcome"], "interrupted")
        self.assertTrue(summary["output_off_verified"])
        self.assertFalse(self.device.enabled)
        self.device.disconnect.assert_awaited_once()

    async def test_setpoint_mismatch_prevents_output_enable(self):
        self.device.preparation_mismatch = True

        exit_code = await self.run_runner()
        summary = self.read_summary()

        self.assertEqual(exit_code, 1)
        self.assertIn("Setpoint readback mismatch", summary["reason"])
        self.device.enable_output.assert_not_awaited()
        self.assertFalse(self.device.enabled)

    async def test_dead_reader_prevents_output_enable(self):
        self.device.transport.reader_task.done.return_value = True

        exit_code = await self.run_runner()
        summary = self.read_summary()

        self.assertEqual(exit_code, 1)
        self.assertIn("receive task", summary["reason"])
        self.device.enable_output.assert_not_awaited()

    async def test_protection_trip_triggers_shutdown(self):
        self.device.active_sample_override = {
            "protection": "OVP",
            "protection_code": 1,
        }

        exit_code = await self.run_runner()
        summary = self.read_summary()

        self.assertEqual(exit_code, 1)
        self.assertIn("Device protection", summary["reason"])
        self.assertFalse(self.device.enabled)

    async def test_unexpected_output_off_is_fault(self):
        self.device.active_sample_override = {
            "output_enabled": False,
        }

        exit_code = await self.run_runner()
        summary = self.read_summary()

        self.assertEqual(exit_code, 1)
        self.assertIn("OFF unexpectedly", summary["reason"])

    async def test_overvoltage_triggers_shutdown(self):
        self.device.active_sample_override = {
            "output_voltage_V": 3.20,
        }

        exit_code = await self.run_runner()
        summary = self.read_summary()

        self.assertEqual(exit_code, 1)
        self.assertIn("voltage-monitoring threshold", summary["reason"])
        self.assertFalse(self.device.enabled)

    async def test_setpoint_change_during_run_is_fault(self):
        self.device.active_sample_override = {
            "set_current_A": 0.200,
        }

        exit_code = await self.run_runner()
        summary = self.read_summary()

        self.assertEqual(exit_code, 1)
        self.assertIn("Setpoint readback mismatch", summary["reason"])
        self.assertFalse(self.device.enabled)

    async def test_failed_off_commands_do_not_report_completion(self):
        async def fail_cleanup_only():
            if self.device.enabled:
                raise ConnectionError("Simulated lost connection.")
            self.device.enabled = False

        self.device.disable_output.side_effect = fail_cleanup_only

        exit_code = await self.run_runner()
        summary = self.read_summary()

        self.assertEqual(exit_code, 1)
        self.assertEqual(summary["outcome"], "fault")
        self.assertFalse(summary["output_off_verified"])
        self.assertIn("could not be verified", summary["reason"])

        # Demonstrates the actual communication-loss limitation:
        # software failure handling does not guarantee physical output off.
        self.assertTrue(self.device.enabled)
        self.assertEqual(self.device.disable_output.await_count, 3)
        self.device.disconnect.assert_awaited_once()

    async def test_unverified_off_state_is_fault(self):
        original_read = self.device._read

        async def read_with_bad_shutdown_state(timeout_s=3.0):
            sample = await original_read(timeout_s=timeout_s)

            if self.device.enable_output.await_count and not self.device.enabled:
                return replace(sample, output_enabled=True)

            return sample

        self.device.read_fresh.side_effect = read_with_bad_shutdown_state

        exit_code = await self.run_runner()
        summary = self.read_summary()

        self.assertEqual(exit_code, 1)
        self.assertEqual(summary["outcome"], "fault")
        self.assertFalse(summary["output_off_verified"])
        self.device.disconnect.assert_awaited_once()

    async def test_non_cv_sample_is_not_cv_qualified(self):
        self.device.active_sample_override = {"mode": "CC"}
        self.monitor.update.return_value = MonitorResult(
            status=Status.FAULT,
            reason="Simulated end of test.",
            termination_elapsed_s=0.0,
        )

        await self.run_runner()

        arguments = self.monitor.update.call_args.kwargs
        self.assertFalse(arguments["cv_qualified"])
        self.assertEqual(
            self.read_rows()[0]["supply_cv_qualified"],
            "False",
        )

    async def test_low_voltage_cv_sample_is_not_qualified(self):
        self.device.active_sample_override = {
            "mode": "CV",
            "output_voltage_V": 0.1,
        }
        self.monitor.update.return_value = MonitorResult(
            status=Status.FAULT,
            reason="Simulated end of test.",
            termination_elapsed_s=0.0,
        )

        await self.run_runner()

        arguments = self.monitor.update.call_args.kwargs
        self.assertFalse(arguments["cv_qualified"])


class ChargerCliTests(unittest.TestCase):
    def setUp(self):
        self.config = example_config()

        self.loader = self.enterContext(
            patch("charger.load_charge_config", return_value=self.config)
        )
        self.setup_check = self.enterContext(
            patch("charger.check_setup", return_value=[])
        )
        self.device_factory = self.enterContext(
            patch("charger.FreshDPS150")
        )

        self.enterContext(redirect_stdout(io.StringIO()))

    def call_main(self, *arguments):
        with patch(
            "sys.argv",
            ["charger.py", "unused.yaml", *arguments],
        ):
            return charger.main()

    def test_default_is_dry_run_without_device_creation(self):
        exit_code = self.call_main()

        self.assertEqual(exit_code, 0)
        self.device_factory.assert_not_called()

    def test_preflight_failure_prevents_device_creation(self):
        self.setup_check.return_value = ["Simulated invalid setup."]

        exit_code = self.call_main("--run")

        self.assertEqual(exit_code, 1)
        self.device_factory.assert_not_called()

    def test_example_only_profile_cannot_run(self):
        self.loader.return_value = replace(
            self.config,
            model="EXAMPLE_ONLY",
        )

        with patch("sys.stderr", new_callable=io.StringIO):
            exit_code = self.call_main("--run")

        self.assertEqual(exit_code, 1)
        self.device_factory.assert_not_called()

    def test_declining_confirmation_prevents_device_creation(self):
        with patch("builtins.input", return_value="NO"):
            exit_code = self.call_main("--run")

        self.assertEqual(exit_code, 0)
        self.device_factory.assert_not_called()

    def test_invalid_configuration_prevents_device_creation(self):
        self.loader.side_effect = ConfigError("Simulated invalid YAML.")

        with patch("sys.stderr", new_callable=io.StringIO):
            exit_code = self.call_main("--run")

        self.assertEqual(exit_code, 1)
        self.device_factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()