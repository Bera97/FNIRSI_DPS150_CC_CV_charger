import argparse
import math
import sys
from pathlib import Path

import yaml

from charge_config import ConfigError, load_charge_config


MAX_SETUP_VOLTAGE_V = 9.00
MIN_SETUP_CURRENT_A = 0.010

# Initial software ceiling, not a claim of validated hardware performance.
MAX_SETUP_CURRENT_A = 0.100

VOLTAGE_STEP_V = 0.01
CURRENT_STEP_A = 0.001


def matches_step(value, step):
    units = value / step
    return math.isclose(
        units,
        round(units),
        rel_tol=0.0,
        abs_tol=1e-7,
    )


def check_setup(config):
    """Return configuration/setup conflicts. Never access hardware."""
    errors = []

    if config.cv_voltage_V > MAX_SETUP_VOLTAGE_V:
        errors.append(
            f"CV voltage {config.cv_voltage_V:g} V exceeds "
            f"the current setup ceiling of {MAX_SETUP_VOLTAGE_V:g} V."
        )

    if config.charge_current_A < MIN_SETUP_CURRENT_A:
        errors.append(
            f"Charge current {config.charge_current_A:g} A is below "
            f"the established minimum setting of {MIN_SETUP_CURRENT_A:g} A."
        )

    if config.charge_current_A > MAX_SETUP_CURRENT_A:
        errors.append(
            f"Charge current {config.charge_current_A:g} A exceeds "
            f"the initial commissioning ceiling of "
            f"{MAX_SETUP_CURRENT_A:g} A."
        )

    if not matches_step(config.cv_voltage_V, VOLTAGE_STEP_V):
        errors.append(
            f"CV voltage must match {VOLTAGE_STEP_V:g} V setting increments. "
            "No automatic rounding is applied."
        )

    if not matches_step(config.charge_current_A, CURRENT_STEP_A):
        errors.append(
            f"Resolved charge current must match "
            f"{CURRENT_STEP_A:g} A setting increments. "
            "No automatic rounding is applied."
        )

    return errors


def main():
    parser = argparse.ArgumentParser(
        description="Hardware-free charging setup preflight."
    )
    parser.add_argument(
        "config",
        type=Path,
        help="Path to the charging YAML file.",
    )
    args = parser.parse_args()

    try:
        config = load_charge_config(args.config)
    except (ConfigError, OSError, UnicodeError, yaml.YAMLError) as exc:
        print(f"CONFIGURATION ERROR: {exc}", file=sys.stderr)
        return 1

    errors = check_setup(config)

    print("CHARGING PREFLIGHT — HARDWARE-FREE")
    print("No device connection or output commands.")
    print()
    print(f"Cell:                {config.model}")
    print(f"Chemistry:           {config.chemistry}")
    print(f"Requested current:   {config.charge_current_A:g} A")
    print(f"Requested CV target: {config.cv_voltage_V:g} V")
    print(f"Termination current: {config.termination_current_A:g} A")
    print()

    if errors:
        print("Setup parameter check: FAILED")
        for error in errors:
            print(f"  - {error}")
    else:
        print("Setup parameter check: PASSED")

    print()
    print("Live charging readiness: NOT ESTABLISHED")
    print("Outstanding checks:")
    print("  - Actual DUT datasheet and permitted charging limits.")
    print("  - Initial DUT voltage and polarity.")
    print("  - Ideal-diode model, voltage range, and blocking behavior.")
    print("  - Cell-side versus supply-side voltage qualification.")
    print("  - Current measurement accuracy near termination.")
    print("  - Communication-loss and shutdown behavior.")
    print("  - Load validation at the selected charging current.")
    print()
    print("Passing this check does not authorize hardware charging.")

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())