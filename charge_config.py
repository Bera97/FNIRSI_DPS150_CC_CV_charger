import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml


class ConfigError(ValueError):
    """Invalid charging configuration."""


class UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that also rejects duplicate keys."""


def construct_unique_mapping(loader, node):
    mapping = {}

    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)

        if not isinstance(key, str):
            raise ConfigError("All YAML mapping keys must be strings.")

        if key in mapping:
            raise ConfigError(f"Duplicate YAML key: {key}")

        mapping[key] = loader.construct_object(value_node)

    return mapping


UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    construct_unique_mapping,
)


@dataclass(frozen=True)
class ChargeConfig:
    model: str
    chemistry: str
    capacity_Ah: float | None
    capacitance_F: float | None

    max_current_A: float
    current_method: str
    current_selection: float
    charge_current_A: float

    cv_voltage_V: float
    termination_current_A: float
    termination_hold_s: float
    timeout_s: float


def check_mapping(value, name, required_keys, optional_keys=()):
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be a YAML mapping.")

    allowed = set(required_keys) | set(optional_keys)
    unknown = set(value) - allowed
    missing = set(required_keys) - set(value)

    if unknown:
        raise ConfigError(
            f"{name}: unknown keys: {', '.join(sorted(unknown))}"
        )

    if missing:
        raise ConfigError(
            f"{name}: missing keys: {', '.join(sorted(missing))}"
        )

    return value


def positive_number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{name} must be a number greater than zero.")

    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ConfigError(f"{name} must be a finite number.") from None

    if not math.isfinite(result) or result <= 0:
        raise ConfigError(f"{name} must be finite and greater than zero.")

    return result


def optional_positive_number(value, name):
    if value is None:
        return None

    return positive_number(value, name)


def nonempty_text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{name} must be nonempty text.")

    return value.strip()


def load_charge_config(path):
    path = Path(path)

    with path.open("r", encoding="utf-8") as stream:
        document = yaml.load(stream, Loader=UniqueKeySafeLoader)

    root = check_mapping(
        document,
        "configuration",
        required_keys={"cell", "charge"},
    )

    cell = check_mapping(
        root["cell"],
        "cell",
        required_keys={"model", "chemistry"},
        optional_keys={"capacity_Ah", "capacitance_F"},
    )

    charge = check_mapping(
        root["charge"],
        "charge",
        required_keys={
            "max_current_A",
            "cv_voltage_V",
            "termination_current_A",
            "termination_hold_s",
            "timeout_s",
        },
        optional_keys={
            "current_A",
            "current_fraction",
            "c_rate",
        },
    )

    model = nonempty_text(cell["model"], "cell.model")
    chemistry = nonempty_text(cell["chemistry"], "cell.chemistry")

    capacity = optional_positive_number(
        cell.get("capacity_Ah"),
        "cell.capacity_Ah",
    )
    capacitance = optional_positive_number(
        cell.get("capacitance_F"),
        "cell.capacitance_F",
    )

    maximum_current = positive_number(
        charge["max_current_A"],
        "charge.max_current_A",
    )

    methods = ("current_A", "current_fraction", "c_rate")
    selected = [
        name for name in methods
        if charge.get(name) is not None
    ]

    if len(selected) != 1:
        raise ConfigError(
            "Set exactly one of current_A, current_fraction, or c_rate; "
            "leave the others null."
        )

    method = selected[0]
    selection = positive_number(
        charge[method],
        f"charge.{method}",
    )

    if method == "current_A":
        requested_current = selection

    elif method == "current_fraction":
        if selection > 1:
            raise ConfigError(
                "charge.current_fraction must be greater than 0 "
                "and no greater than 1."
            )

        requested_current = maximum_current * selection

    else:
        if capacity is None:
            raise ConfigError(
                "cell.capacity_Ah is required when using c_rate."
            )

        requested_current = capacity * selection

    if not math.isfinite(requested_current) or requested_current <= 0:
        raise ConfigError("Calculated charging current is invalid.")

    if requested_current > maximum_current:
        raise ConfigError(
            f"Requested charging current {requested_current:.6g} A "
            f"exceeds max_current_A {maximum_current:.6g} A."
        )

    cv_voltage = positive_number(
        charge["cv_voltage_V"],
        "charge.cv_voltage_V",
    )
    termination_current = positive_number(
        charge["termination_current_A"],
        "charge.termination_current_A",
    )
    termination_hold = positive_number(
        charge["termination_hold_s"],
        "charge.termination_hold_s",
    )
    timeout = positive_number(
        charge["timeout_s"],
        "charge.timeout_s",
    )

    if termination_current >= requested_current:
        raise ConfigError(
            "termination_current_A must be below the resolved "
            "charging current."
        )

    if termination_hold >= timeout:
        raise ConfigError(
            "termination_hold_s must be shorter than timeout_s."
        )

    return ChargeConfig(
        model=model,
        chemistry=chemistry,
        capacity_Ah=capacity,
        capacitance_F=capacitance,
        max_current_A=maximum_current,
        current_method=method,
        current_selection=selection,
        charge_current_A=requested_current,
        cv_voltage_V=cv_voltage,
        termination_current_A=termination_current,
        termination_hold_s=termination_hold,
        timeout_s=timeout,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Validate a charging YAML file without accessing hardware."
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

    print("Configuration validation passed.")
    print("DRY RUN ONLY — no hardware connection or output commands.")
    print()
    print(f"Cell model:             {config.model}")
    print(f"Chemistry:              {config.chemistry}")

    if config.capacity_Ah is not None:
        print(f"Capacity:               {config.capacity_Ah:g} Ah")

    if config.capacitance_F is not None:
        print(f"Capacitance:            {config.capacitance_F:g} F")

    print(f"Maximum charge current: {config.max_current_A:g} A")
    print(
        f"Current selection:      {config.current_method} "
        f"= {config.current_selection:g}"
    )
    print(f"Resolved charge current:{config.charge_current_A: .6g} A")
    print(f"CV voltage:             {config.cv_voltage_V:g} V")
    print(f"Termination current:    {config.termination_current_A:g} A")
    print(f"Termination hold:       {config.termination_hold_s:g} s")
    print(f"Charge timeout:         {config.timeout_s:g} s")
    print()
    print(
        "This validates configuration consistency only, not cell safety, "
        "instrument capability, or termination measurement accuracy."
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())