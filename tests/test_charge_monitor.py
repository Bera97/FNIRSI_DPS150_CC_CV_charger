import unittest

from charge_monitor import ChargeMonitor, Status


class ChargeMonitorTests(unittest.TestCase):
    def make_monitor(self, **overrides):
        settings = {
            "termination_current_A": 0.020,
            "termination_hold_s": 3.0,
            "timeout_s": 20.0,
            "start_time_s": 0.0,
            "max_sample_gap_s": 2.0,
        }
        settings.update(overrides)
        return ChargeMonitor(**settings)

    def update(self, monitor, time, current=0.010, cv=True):
        return monitor.update(
            now_s=time,
            current_A=current,
            cv_qualified=cv,
        )

    def test_continuous_hold_completes(self):
        monitor = self.make_monitor()

        for time in (0, 1, 2):
            result = self.update(monitor, time)
            self.assertIs(result.status, Status.CHARGING)

        result = self.update(monitor, 3)
        self.assertIs(result.status, Status.COMPLETE)
        self.assertEqual(result.termination_elapsed_s, 3.0)

    def test_low_current_without_cv_does_not_complete(self):
        monitor = self.make_monitor()

        for time in range(6):
            result = self.update(monitor, time, cv=False)
            self.assertIs(result.status, Status.CHARGING)
            self.assertEqual(result.termination_elapsed_s, 0.0)

    def test_high_current_resets_hold(self):
        monitor = self.make_monitor()

        self.update(monitor, 0)
        self.update(monitor, 1)
        self.update(monitor, 2, current=0.050)

        for time in (3, 4, 5):
            result = self.update(monitor, time)
            self.assertIs(result.status, Status.CHARGING)

        self.assertIs(
            self.update(monitor, 6).status,
            Status.COMPLETE,
        )

    def test_loss_of_cv_resets_hold(self):
        monitor = self.make_monitor()

        self.update(monitor, 0)
        self.update(monitor, 1)
        result = self.update(monitor, 2, cv=False)

        self.assertEqual(result.termination_elapsed_s, 0.0)

        for time in (3, 4, 5):
            self.assertIs(
                self.update(monitor, time).status,
                Status.CHARGING,
            )

        self.assertIs(
            self.update(monitor, 6).status,
            Status.COMPLETE,
        )

    def test_exact_current_threshold_qualifies(self):
        monitor = self.make_monitor()

        for time in range(4):
            result = self.update(monitor, time, current=0.020)

        self.assertIs(result.status, Status.COMPLETE)

    def test_timeout_is_fault(self):
        monitor = self.make_monitor(
            timeout_s=4.0,
            max_sample_gap_s=5.0,
        )

        self.update(monitor, 0, cv=False)
        result = self.update(monitor, 4, cv=False)

        self.assertIs(result.status, Status.FAULT)
        self.assertIn("timeout", result.reason.lower())

    def test_timeout_takes_priority_over_completion(self):
        monitor = self.make_monitor(
            timeout_s=4.0,
            max_sample_gap_s=5.0,
        )

        self.update(monitor, 1)
        result = self.update(monitor, 4)

        self.assertIs(result.status, Status.FAULT)

    def test_telemetry_gap_is_fault(self):
        monitor = self.make_monitor()

        self.update(monitor, 0)
        result = self.update(monitor, 3)

        self.assertIs(result.status, Status.FAULT)
        self.assertIn("gap", result.reason.lower())

    def test_late_first_sample_is_fault(self):
        monitor = self.make_monitor()

        result = self.update(monitor, 3)

        self.assertIs(result.status, Status.FAULT)

    def test_duplicate_timestamp_is_fault(self):
        monitor = self.make_monitor()

        self.update(monitor, 0)
        self.assertIs(
            self.update(monitor, 0).status,
            Status.FAULT,
        )

    def test_backwards_timestamp_is_fault(self):
        monitor = self.make_monitor()

        self.update(monitor, 1)
        self.assertIs(
            self.update(monitor, 0.5).status,
            Status.FAULT,
        )

    def test_invalid_current_is_fault(self):
        for current in (-0.001, float("nan"), float("inf"), True):
            with self.subTest(current=current):
                monitor = self.make_monitor()
                result = self.update(monitor, 0, current=current)
                self.assertIs(result.status, Status.FAULT)

    def test_nonboolean_cv_is_fault(self):
        monitor = self.make_monitor()

        result = self.update(monitor, 0, cv="yes")

        self.assertIs(result.status, Status.FAULT)

    def test_complete_is_latched(self):
        monitor = self.make_monitor()

        for time in range(4):
            completed = self.update(monitor, time)

        later = self.update(monitor, 4, current=0.100, cv=False)

        self.assertIs(later, completed)
        self.assertIs(later.status, Status.COMPLETE)

    def test_fault_is_latched(self):
        monitor = self.make_monitor()

        fault = self.update(monitor, 0, current=-0.001)
        later = self.update(monitor, 1)

        self.assertIs(later, fault)
        self.assertIs(later.status, Status.FAULT)


if __name__ == "__main__":
    unittest.main()