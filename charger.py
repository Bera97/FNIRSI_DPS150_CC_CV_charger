import argparse
import asyncio
import csv
import json
import math
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

from charge_config import ConfigError, load_charge_config
from charge_monitor import ChargeMonitor, Status
from charge_preflight import check_setup
from dps150_adapter import FreshDPS150


DEFAULT_PORT = "COM3"

COMMAND_TIMEOUT_S = 3.0
READ_TIMEOUT_S = 1.5
POLL_INTERVAL_S = 1.0
MAX_SAMPLE_GAP_S = 3.0

CV_TOLERANCE_V = 0.03
OVERVOLTAGE_MARGIN_V = 0.05

SET_VOLTAGE_TOLERANCE_V = 0.0051
SET_CURRENT_TOLERANCE_A = 0.00051

CSV_FIELDS = [
    "utc",
    "elapsed_s",
    "input_voltage_V",
    "supply_voltage_V",
    "supply_current_A",
    "supply_power_W",
    "device_temperature_C",
    "set_voltage_V",
    "set_current_A",
    "output_enabled",
    "mode",
    "protection",
    "supply_cv_qualified",
    "termination_elapsed_s",
    "status",
    "reason",
]


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def show_plan(config, port):
    print("\nDPS150 CC-CV CHARGER")
    print(f"Port:                  {port}")
    print(f"Cell/model:            {config.model}")
    print(f"Chemistry:             {config.chemistry}")
    print(f"Current method:        {config.current_method}")
    print(f"Charge current:        {config.charge_current_A:g} A")
    print(f"Maximum permitted:     {config.max_current_A:g} A")
    print(f"CV command:            {config.cv_voltage_V:g} V")
    print(f"Termination current:   {config.termination_current_A:g} A")
    print(f"Termination hold:      {config.termination_hold_s:g} s")
    print(f"Charge timeout:        {config.timeout_s:g} s")
    print()
    print("Voltage measurement: SUPPLY SIDE")
    print("Voltage-drop compensation: NONE")
    print(
        f"CV qualification: reported CV mode and supply voltage "
        f"within {CV_TOLERANCE_V:g} V of target."
    )
    print(
        "This does not independently verify the cell-terminal voltage "
        "or termination-current accuracy."
    )


async def bounded(awaitable, timeout_s=COMMAND_TIMEOUT_S):
    return await asyncio.wait_for(awaitable, timeout=timeout_s)


def check_reader(device):
    transport = device.transport

    if transport is None or not transport.is_connected:
        raise RuntimeError("Serial transport is not connected.")

    if transport.reader_task is None or transport.reader_task.done():
        raise RuntimeError("Serial receive task is not running.")


def check_sample_age(sample, now):
    age = now - sample.received_at_s

    if not math.isfinite(age) or age < 0 or age > MAX_SAMPLE_GAP_S:
        raise RuntimeError(f"Invalid or stale sample age: {age:g} s")


def check_settings(sample, config):
    voltage_ok = math.isclose(
        sample.set_voltage_V,
        config.cv_voltage_V,
        rel_tol=0.0,
        abs_tol=SET_VOLTAGE_TOLERANCE_V,
    )
    current_ok = math.isclose(
        sample.set_current_A,
        config.charge_current_A,
        rel_tol=0.0,
        abs_tol=SET_CURRENT_TOLERANCE_A,
    )

    if not voltage_ok or not current_ok:
        raise RuntimeError(
            "Setpoint readback mismatch: "
            f"{sample.set_voltage_V:g} V / "
            f"{sample.set_current_A:g} A."
        )


def check_protection(sample):
    if sample.protection_code != 0:
        raise RuntimeError(f"Device protection: {sample.protection}")


async def shutdown_output(device):
    """Best-effort OFF transmission followed by fresh state verification."""
    print("\nShutdown: commanding output OFF...", flush=True)

    command_sent = False

    for attempt in range(1, 3):
        try:
            await bounded(device.disable_output())
            command_sent = True
            break
        except Exception as exc:
            print(
                f"OFF attempt {attempt} failed: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            await asyncio.sleep(0.1)

    if not command_sent:
        print(
            "WARNING: output-OFF command could not be sent. "
            "Switch output OFF manually.",
            flush=True,
        )
        return False

    try:
        sample = await device.read_fresh(timeout_s=2.0)

        if sample.output_enabled:
            raise RuntimeError("Device still reports output ON.")

        print(
            "Fresh device state reports output OFF. "
            f"Supply voltage readback: {sample.output_voltage_V:.4f} V",
            flush=True,
        )
        return True

    except Exception as exc:
        print(
            f"Output-OFF transmission succeeded, but verification failed: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )
        print(
            "Confirm output OFF manually. "
            "A transmitted command is not verified shutdown.",
            flush=True,
        )
        return False


async def run_charge(config, port, run_directory):
    device = FreshDPS150(port=port)
    loop = asyncio.get_running_loop()

    outcome = "fault"
    reason = "Run did not finish."
    output_off_verified = False
    started = None

    csv_path = run_directory / "measurements.csv"

    try:
        with csv_path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
            writer.writeheader()
            stream.flush()

            print(f"\nConnecting to {port}...", flush=True)
            await bounded(device.connect(), timeout_s=10.0)
            check_reader(device)

            await bounded(device.disable_output())
            sample = await device.read_fresh(timeout_s=READ_TIMEOUT_S)

            if sample.output_enabled:
                raise RuntimeError("Could not establish output OFF.")

            check_protection(sample)

            if sample.input_voltage_V <= config.cv_voltage_V:
                raise RuntimeError(
                    f"Input voltage {sample.input_voltage_V:g} V "
                    "does not exceed the configured output ceiling."
                )

            print("Writing charging setpoints...", flush=True)
            await bounded(device.set_current(config.charge_current_A))
            await bounded(device.set_voltage(config.cv_voltage_V))

            sample = await device.read_fresh(timeout_s=READ_TIMEOUT_S)
            check_settings(sample, config)
            check_protection(sample)

            if sample.output_enabled:
                raise RuntimeError(
                    "Output unexpectedly enabled during setup."
                )

            print("Setpoints verified. Output remains OFF.", flush=True)

            for remaining in (3, 2, 1):
                print(f"Output ON in {remaining}...", flush=True)
                await asyncio.sleep(1)

            started = loop.time()
            deadline = started + config.timeout_s

            monitor = ChargeMonitor(
                termination_current_A=config.termination_current_A,
                termination_hold_s=config.termination_hold_s,
                timeout_s=config.timeout_s,
                start_time_s=started,
                max_sample_gap_s=MAX_SAMPLE_GAP_S,
            )

            # Include the enable command in the active-phase timeout.
            await bounded(
                device.enable_output(),
                timeout_s=min(COMMAND_TIMEOUT_S, config.timeout_s),
            )
            print("Output enabled; monitoring started.", flush=True)

            while True:
                remaining = deadline - loop.time()

                if remaining <= 0:
                    raise TimeoutError("Charge timeout reached.")

                check_reader(device)

                sample = await device.read_fresh(
                    timeout_s=min(READ_TIMEOUT_S, remaining)
                )
                now = loop.time()

                if now >= deadline:
                    raise TimeoutError("Charge timeout reached.")

                check_sample_age(sample, now)
                check_protection(sample)
                check_settings(sample, config)

                if not sample.output_enabled:
                    raise RuntimeError(
                        "Output switched OFF unexpectedly during charging."
                    )

                if sample.output_voltage_V < -OVERVOLTAGE_MARGIN_V:
                    raise RuntimeError("Unexpected negative supply voltage.")

                if (
                    sample.output_voltage_V
                    > config.cv_voltage_V + OVERVOLTAGE_MARGIN_V
                ):
                    raise RuntimeError(
                        "Supply voltage exceeded the configured "
                        "voltage-monitoring threshold."
                    )

                if sample.input_voltage_V <= config.cv_voltage_V:
                    raise RuntimeError(
                        "Input voltage fell below the required output ceiling."
                    )

                cv_qualified = (
                    sample.mode == "CV"
                    and abs(
                        sample.output_voltage_V - config.cv_voltage_V
                    ) <= CV_TOLERANCE_V
                )

                result = monitor.update(
                    now_s=sample.received_at_s,
                    current_A=sample.output_current_A,
                    cv_qualified=cv_qualified,
                )

                elapsed = sample.received_at_s - started

                writer.writerow({
                    "utc": utc_now(),
                    "elapsed_s": f"{elapsed:.6f}",
                    "input_voltage_V": sample.input_voltage_V,
                    "supply_voltage_V": sample.output_voltage_V,
                    "supply_current_A": sample.output_current_A,
                    "supply_power_W": sample.output_power_W,
                    "device_temperature_C": sample.device_temperature_C,
                    "set_voltage_V": sample.set_voltage_V,
                    "set_current_A": sample.set_current_A,
                    "output_enabled": sample.output_enabled,
                    "mode": sample.mode,
                    "protection": sample.protection,
                    "supply_cv_qualified": cv_qualified,
                    "termination_elapsed_s": result.termination_elapsed_s,
                    "status": result.status.value,
                    "reason": result.reason,
                })
                stream.flush()

                print(
                    f"t={elapsed:8.1f} s | "
                    f"V={sample.output_voltage_V:.4f} V | "
                    f"I={sample.output_current_A * 1000:.2f} mA | "
                    f"{sample.mode} | "
                    f"CV_ok={cv_qualified} | "
                    f"hold={result.termination_elapsed_s:.1f}/"
                    f"{config.termination_hold_s:g} s",
                    flush=True,
                )

                if result.status is Status.FAULT:
                    raise RuntimeError(result.reason)

                if result.status is Status.COMPLETE:
                    outcome = "complete"
                    reason = result.reason
                    break

                await asyncio.sleep(
                    max(
                        0.0,
                        min(POLL_INTERVAL_S, deadline - loop.time()),
                    )
                )

    except asyncio.CancelledError:
        outcome = "interrupted"
        reason = "User interruption or task cancellation."

    except Exception as exc:
        outcome = "fault"
        reason = f"{type(exc).__name__}: {exc}"
        print(f"\nFAULT: {reason}", flush=True)

    finally:
        try:
            output_off_verified = await shutdown_output(device)
        except Exception as exc:
            print(f"Shutdown error: {exc}", flush=True)
            output_off_verified = False

        try:
            await bounded(device.disconnect())
            print("Connection closed.", flush=True)
        except Exception as exc:
            print(f"Disconnect warning: {exc}", flush=True)

        if not output_off_verified:
            outcome = "fault"
            reason += " Output-OFF state could not be verified."

        summary = {
            "finished_utc": utc_now(),
            "outcome": outcome,
            "reason": reason,
            "output_off_verified": output_off_verified,
            "elapsed_s": (
                None if started is None else loop.time() - started
            ),
            "port": port,
            "voltage_measurement": "supply_side",
            "voltage_drop_compensation": "none",
            "resolved_config": asdict(config),
        }

        summary_path = run_directory / "result.json"

        try:
            summary_path.write_text(
                json.dumps(summary, indent=2, allow_nan=False) + "\n",
                encoding="utf-8",
            )
        except Exception as exc:
            print(f"Could not save result summary: {exc}", flush=True)
            outcome = "fault"

        print(f"\nResult: {outcome.upper()}", flush=True)
        print(f"Reason: {reason}", flush=True)
        print(f"Logs: {run_directory.resolve()}", flush=True)

    if outcome == "complete":
        return 0
    if outcome == "interrupted":
        return 130
    return 1


def main():
    parser = argparse.ArgumentParser(
        description="YAML-controlled DPS150 CC-CV charger."
    )
    parser.add_argument("config", type=Path)
    parser.add_argument("--port", default=DEFAULT_PORT)
    parser.add_argument(
        "--run",
        action="store_true",
        help="Enable hardware execution after interactive confirmation.",
    )
    args = parser.parse_args()

    try:
        config = load_charge_config(args.config)
    except (ConfigError, OSError, UnicodeError, yaml.YAMLError) as exc:
        print(f"CONFIGURATION ERROR: {exc}", file=sys.stderr)
        return 1

    show_plan(config, args.port)

    errors = check_setup(config)

    if errors:
        print("\nSETUP CHECK FAILED:")
        for error in errors:
            print(f"  - {error}")
        return 1

    print("\nSetup parameter check passed.")

    if not args.run:
        print("DRY RUN ONLY — no connection and no output commands.")
        print("Use --run only after checking the load and charging limits.")
        return 0

    if config.model.upper() == "EXAMPLE_ONLY":
        print(
            "\nRefusing hardware execution with the EXAMPLE_ONLY profile.",
            file=sys.stderr,
        )
        return 1

    print("\nBefore continuing, verify:")
    print("  - Correct polarity and wiring.")
    print("  - The chosen limits are appropriate for the connected DUT.")
    print("  - Reverse-current protection is installed where required.")
    print("  - Initial cell voltage and temperature are acceptable.")
    print("  - No other instrument is driving the DUT.")
    print("  - The current threshold is meaningful for the measurement range.")
    print("  - You can manually switch the output off.")
    print()
    print(
        "No cell-temperature sensing, recovery/precharge, cell balancing, "
        "or independent hardware watchdog is implemented."
    )

    try:
        answer = input("\nType START to enable the output: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled. No hardware connection opened.")
        return 130

    if answer != "START":
        print("Cancelled. No hardware connection opened.")
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    run_directory = Path("logs") / stamp

    try:
        run_directory.mkdir(parents=True, exist_ok=False)
        (run_directory / "resolved_config.json").write_text(
            json.dumps(asdict(config), indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"Cannot prepare logging: {exc}", file=sys.stderr)
        return 1

    try:
        return asyncio.run(
            run_charge(config, args.port, run_directory)
        )
    except KeyboardInterrupt:
        print(
            "\nInterrupted again. Software cleanup may not have finished. "
            "Confirm output OFF manually.",
            file=sys.stderr,
        )
        return 130


if __name__ == "__main__":
    raise SystemExit(main())