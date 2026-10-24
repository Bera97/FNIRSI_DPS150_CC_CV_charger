# FNIRSI DPS150 CC–CV Charger

Python command-line application for supervised CC–CV charging using an FNIRSI DPS150 power supply. Charging parameters are configured in YAML.

## Safety and limitations

This is a supervised bench-charger prototype, not an unattended charger.

- The DPS150 remains on if the PC/data connection is lost. Software cannot guarantee shutdown through a disconnected interface.
- Voltage is measured at the supply, not independently at the cell terminals.
- No automatic diode-drop compensation is applied.
- Low-current readback accuracy must be validated before relying on a termination threshold.
- No cell-temperature sensing, precharge/recovery, balancing, float charging, or independent watchdog is implemented.
- Output-disabled state does not mean an attached cell or capacitor is discharged.

Use datasheet-approved charging parameters, verify polarity and initial cell condition, and keep a manual shutdown method accessible.

## Features

- Charging current selected in amperes, C-rate, or a fraction of maximum current.
- Configuration and provisional setup-limit checks.
- Default dry-run mode and confirmation before hardware execution.
- Setpoint readback verification and fresh-response acquisition.
- CV-qualified termination-current hold and charging timeout.
- CSV measurements and JSON configuration/result logging.
- Shutdown attempts on completion, interruption, and faults.
- No automatic reconnect or restart.

## Setup

Run commands from the project root in VS Code's PowerShell terminal.

Activate the existing environment:

```powershell
.\.DPS150venv\Scripts\Activate.ps1
```

Install the local DPS150 library and YAML dependency:

```powershell
python -m pip install .
python -m pip install PyYAML
```

Select `.DPS150venv\Scripts\python.exe` as the VS Code interpreter.

Upstream library: https://github.com/KochC/DPS-150-python-library

## Configuration

Save your profile as `configs/charge.yaml`:

```yaml
cell:
  model: null
  chemistry: null
  capacity_Ah: null              # Required for C-rate.
  capacitance_F: null            # Optional LIC/supercapacitor metadata.

charge:
  max_current_A: null            # DUT's permitted maximum charge current.

  # Set exactly ONE option; leave the others null.
  current_A: null
  current_fraction: 0.5
  c_rate: null

  cv_voltage_V: null

  termination_current_A: null
  termination_hold_s: 30
  timeout_s: 7200
```

Replace required `null` values with verified DUT parameters. The example hold and timeout are not cell-specific recommendations.

| Current option | Calculation |
|---|---|
| `current_A` | Specified amperes |
| `current_fraction` | `max_current_A × current_fraction` |
| `c_rate` | `capacity_Ah × c_rate` |

The resolved current must not exceed `max_current_A`. Chemistry is descriptive metadata; it does not automatically validate charging limits.

### Current software limits

Defined in `charge_preflight.py`:

- Maximum voltage command: 9 V.
- Charging-current command: 10–100 mA.
- Voltage/current setting increments: 0.01 V / 0.001 A.

These are provisional project limits, not accuracy guarantees or the supply's full capabilities. Requests are rejected rather than silently rounded or clamped.

## Usage

### Validate configuration

```powershell
python .\charge_config.py .\configs\charge.yaml
python .\charge_preflight.py .\configs\charge.yaml
```

### Dry run — no hardware connection

```powershell
python .\charger.py .\configs\charge.yaml
```

### Hardware execution

```powershell
python .\charger.py .\configs\charge.yaml --run
```

Type `START` when prompted.

To select another port:

```powershell
python .\charger.py .\configs\charge.yaml --port COM5 --run
```

Press `Ctrl+C` once to interrupt and allow cleanup to finish. If shutdown cannot be verified, switch output off manually.

## Charging and termination

The DPS150 performs CC–CV regulation; Python sets the limits and supervises the run.

Completion requires:

1. Reported CV mode.
2. Supply voltage within 0.03 V of the target.
3. Current at or below `termination_current_A`.
4. Those conditions maintained for `termination_hold_s`.

Losing qualification resets the hold. Timeout is a fault, not successful completion.

The runner also checks telemetry, protection state, setpoint changes, unexpected output-off, and excessive supply voltage. It does not automatically program the device's OVP/OCP/OPP/OTP/LVP settings.

## Logs and results

Each confirmed hardware run creates:

```text
logs/<UTC timestamp>/
├── resolved_config.json
├── measurements.csv
└── result.json
```

The CSV records measurements, mode, protection, CV qualification, and hold progress.

| Outcome | Meaning |
|---|---|
| `complete` | Termination condition met and output-disabled state verified |
| `interrupted` | Run cancelled and output-disabled state verified |
| `fault` | Run failed or shutdown could not be verified |

Shutdown verification checks the device's reported output state, not settled zero terminal voltage. Stored voltage/current settings remain unchanged.

## Tests

Run all automated tests without hardware:

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```

At the implementation milestone, 61 tests passed.

Resistor integration profiles:

```powershell
python .\charger.py .\configs\resistor_cc_test.yaml --run
python .\charger.py .\configs\resistor_cv_test.yaml --run
```

Use the specified resistor load—not a cell:

- CC test: expected timeout fault followed by shutdown.
- CV test: expected successful termination followed by shutdown.

Manual interruption and communication-loss handling were also tested. Communication loss correctly produced a fault, but the physical output remained enabled.

## Project files

| File/folder | Purpose |
|---|---|
| `charger.py` | Main runner and logging |
| `charge_config.py` | YAML validation and current calculation |
| `charge_preflight.py` | Provisional setup checks |
| `charge_monitor.py` | Termination and timing logic |
| `dps150_adapter.py` | Fresh complete-state response acquisition |
| `configs/` | YAML profiles |
| `checks/` | Hardware diagnostics |
| `tests/` | Automated tests |
| `logs/` | Hardware-run records |

Fresh acquisition timestamps packet receipt; it does not establish calibration or an instrument acquisition timestamp. Do not run the library's background polling alongside the adapter.

The implementation is complete as a supervised prototype. Validate cell-terminal voltage and current near the intended termination threshold before trusting a real charging endpoint.