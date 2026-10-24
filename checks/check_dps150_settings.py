import asyncio
import math

from dps150 import DPS150


PORT = "COM3"
TIMEOUT_S = 10.0

TEST_VOLTAGE_V = 3.00
TEST_CURRENT_A = 0.100

PARK_VOLTAGE_V = 2.00
PARK_CURRENT_A = 0.010


async def timed(awaitable):
    return await asyncio.wait_for(awaitable, timeout=TIMEOUT_S)


async def read_and_check(device, expected_voltage, expected_current):
    await asyncio.sleep(0.5)
    state = await timed(device.get_all())

    print(f"Readback voltage setting: {state.set_voltage:.4f} V")
    print(f"Readback current setting: {state.set_current:.4f} A")
    print(f"Measured output voltage:  {state.output_voltage:.4f} V")
    print(f"Measured output current:  {state.output_current:.4f} A")
    print(f"Protection state:         {state.protection_state}")

    voltage_ok = math.isclose(
        state.set_voltage,
        expected_voltage,
        rel_tol=0.0,
        abs_tol=0.005,
    )
    current_ok = math.isclose(
        state.set_current,
        expected_current,
        rel_tol=0.0,
        abs_tol=0.0005,
    )

    if not voltage_ok or not current_ok:
        raise RuntimeError("Setpoint readback does not match the requested values.")

    print("Setpoint readback matches.", flush=True)


async def main():
    device = DPS150(port=PORT)

    try:
        print(f"Connecting to {PORT}...", flush=True)
        await timed(device.connect())

        print("Commanding output OFF...", flush=True)
        await timed(device.disable_output())

        print(
            f"\nWriting test settings: "
            f"{TEST_VOLTAGE_V:.2f} V / {TEST_CURRENT_A:.3f} A",
            flush=True,
        )
        await timed(device.set_current(TEST_CURRENT_A))
        await timed(device.set_voltage(TEST_VOLTAGE_V))

        await read_and_check(
            device,
            TEST_VOLTAGE_V,
            TEST_CURRENT_A,
        )

        print(
            "\nCheck the front panel: it should show setpoints of "
            "3.00 V and 0.100 A, with output OFF.",
            flush=True,
        )
        print("Holding these settings for 10 seconds...", flush=True)
        await asyncio.sleep(10)

    finally:
        try:
            print("\nCleanup: commanding output OFF...", flush=True)
            await timed(device.disable_output())

            print("Restoring settings to 2.00 V / 0.010 A...", flush=True)
            await timed(device.set_current(PARK_CURRENT_A))
            await timed(device.set_voltage(PARK_VOLTAGE_V))

            await read_and_check(
                device,
                PARK_VOLTAGE_V,
                PARK_CURRENT_A,
            )

        except Exception as exc:
            print(
                f"Cleanup warning: {type(exc).__name__}: {exc}",
                flush=True,
            )
            print(
                "Manually confirm output OFF and inspect the stored settings.",
                flush=True,
            )

        finally:
            print("Closing connection...", flush=True)
            await timed(device.disconnect())

    print("\nSettings diagnostic completed. Output was never commanded ON.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted. Confirm output OFF on the front panel.")
    except Exception as exc:
        print(f"\nDiagnostic failed: {type(exc).__name__}: {exc}")
        print("Confirm output OFF on the front panel.")
        raise