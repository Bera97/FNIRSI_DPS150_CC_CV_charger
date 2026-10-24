import math
from dataclasses import dataclass
from enum import Enum


class Status(Enum):
    CHARGING = "charging"
    COMPLETE = "complete"
    FAULT = "fault"


@dataclass(frozen=True)
class MonitorResult:
    status: Status
    reason: str
    termination_elapsed_s: float


def finite_number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number.")

    result = float(value)

    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number.")

    return result


def positive_number(value, name):
    result = finite_number(value, name)

    if result <= 0:
        raise ValueError(f"{name} must be greater than zero.")

    return result


class ChargeMonitor:
    """Pure supervisory logic. No instrument access or output control."""

    def __init__(
        self,
        *,
        termination_current_A,
        termination_hold_s,
        timeout_s,
        start_time_s,
        max_sample_gap_s=3.0,
    ):
        self.termination_current_A = positive_number(
            termination_current_A,
            "termination_current_A",
        )
        self.termination_hold_s = positive_number(
            termination_hold_s,
            "termination_hold_s",
        )
        self.timeout_s = positive_number(timeout_s, "timeout_s")
        self.max_sample_gap_s = positive_number(
            max_sample_gap_s,
            "max_sample_gap_s",
        )
        self.start_time_s = finite_number(start_time_s, "start_time_s")

        if self.termination_hold_s >= self.timeout_s:
            raise ValueError("Termination hold must be shorter than timeout.")

        self._last_time = self.start_time_s
        self._has_sample = False
        self._qualifying_since = None
        self._result = MonitorResult(
            Status.CHARGING,
            "Waiting for qualified CV termination.",
            0.0,
        )

    def _fault(self, reason):
        self._result = MonitorResult(Status.FAULT, reason, 0.0)
        return self._result

    def update(self, *, now_s, current_A, cv_qualified):
        # Completion and faults cannot be reversed by later samples.
        if self._result.status is not Status.CHARGING:
            return self._result

        try:
            now = finite_number(now_s, "now_s")
            current = finite_number(current_A, "current_A")
        except (ValueError, OverflowError) as exc:
            return self._fault(str(exc))

        if not isinstance(cv_qualified, bool):
            return self._fault("cv_qualified must be a boolean.")

        if current < 0:
            return self._fault("Negative charging-current reading.")

        if now < self._last_time:
            return self._fault("Timestamp moved backwards.")

        if self._has_sample and now == self._last_time:
            return self._fault("Duplicate timestamp.")

        # Timeout takes priority over a possible completion.
        if now - self.start_time_s >= self.timeout_s:
            return self._fault("Charge timeout reached.")

        if now - self._last_time > self.max_sample_gap_s:
            return self._fault("Telemetry gap exceeded its limit.")

        self._last_time = now
        self._has_sample = True

        qualifies = (
            cv_qualified
            and current <= self.termination_current_A
        )

        if not qualifies:
            self._qualifying_since = None
            self._result = MonitorResult(
                Status.CHARGING,
                "Termination condition not continuously satisfied.",
                0.0,
            )
            return self._result

        if self._qualifying_since is None:
            self._qualifying_since = now

        elapsed = now - self._qualifying_since

        if elapsed >= self.termination_hold_s:
            self._result = MonitorResult(
                Status.COMPLETE,
                "Qualified CV termination-current hold completed.",
                elapsed,
            )
        else:
            self._result = MonitorResult(
                Status.CHARGING,
                "Qualifying termination-current hold.",
                elapsed,
            )

        return self._result