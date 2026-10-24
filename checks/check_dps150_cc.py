import asyncio
import math

from dps150 import DPS150


PORT = "COM3"

VOLTAGE_CEILING_V = 7.00
CURRENT_LIMIT_A = 0.010
LOAD_RESISTANCE_OHM = 470.0

TEST_DURATION_S = 10.0
COMMAND_TIMEOUT_S = 3.0

PARK_VOLTAGE_V = 2.00
PARK_CURRENT_A = 0.010


async def timed(awaitable):
    return await asyncio.wait_for(
        awaitable,
        timeout=COMMAND_TIMEOUT_S,
    )


async def main():
    device = DPS150(port=PORT)
    samples = []

    try:
        print(f"Connecting to {PORT}...", flush=True)
        await timed(device.connect())

        print("Commanding output OFF...", flush=True)
        await timed(device.disable_output())

        print("Setting 7.00 V ceiling / 0.010 A current limit...", flush=True)
        await timed(device.set_current(CURRENT_LIMIT_A))
        await timed(device.set_voltage(VOLTAGE_CEILING_V))
        await asyncio.sleep(0.5)

        state = await timed(device.get_all())

        settings_ok = (
            math.isclose(
                state.set_voltage,
                VOLTAGE_CEILING_V,
                rel_tol=0.0,
                abs_tol=0.005,
            )
            and math.isclose(
                state.set_current,
                CURRENT_LIMIT_A,
                rel_tol=0.0,
                abs_tol=0.0005,
            )
        )

        if not settings_ok:
            raise RuntimeError(
                "Setpoint verification failed; output not enabled."
            )

        if state.protection_state.name != "NORMAL":
            raise RuntimeError(
                f"Protection is not normal: {state.protection_state}"
            )

        print("\nSetpoints verified.", flush=True)
        print("Expected DMM voltage in CC: approximately 4.70 V.", flush=True)
        print("Expected current: approximately 10 mA.", flush=True)
        print("Output will turn ON after this countdown:", flush=True)

        for remaining in (3, 2, 1):
            print(f"{remaining}...", flush=True)
            await asyncio.sleep(1)

        print("\nEnabling output...", flush=True)
        await timed(device.enable_output())

        loop = asyncio.get_running_loop()
        started = loop.time()
        deadline = started + TEST_DURATION_S

        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break

            state = await asyncio.wait_for(
                device.get_all(),
                timeout=min(COMMAND_TIMEOUT_S, remaining),
            )

            elapsed = loop.time() - started
            voltage = state.output_voltage
            current = state.output_current

            if not all(math.isfinite(x) for x in (voltage, current)):
                raise RuntimeError("Invalid measurement readback.")

            estimated_current_mA = (
                voltage / LOAD_RESISTANCE_OHM * 1000
            )

            print(
                f"t={elapsed:5.1f} s | "
                f"V={voltage:.4f} V | "
                f"I_readback={current * 1000:.2f} mA | "
                f"I_from_V/R={estimated_current_mA:.2f} mA | "
                f"Mode={state.mode} | "
                f"Protection={state.protection_state}",
                flush=True,
            )

            if state.protection_state.name != "NORMAL":
                raise RuntimeError(
                    f"Protection triggered: {state.protection_state}"
                )

            if voltage > VOLTAGE_CEILING_V + 0.20:
                raise RuntimeError("Voltage exceeded the software limit.")

            if elapsed >= 2.0:
                samples.append(
                    (voltage, current, state.mode.name)
                )

            await asyncio.sleep(
                max(0.0, min(1.0, deadline - loop.time()))
            )

    finally:
        print("\nCleanup: commanding output OFF...", flush=True)

        try:
            await timed(device.disable_output())
            print("Output-OFF command returned.", flush=True)

            print("Restoring settings to 2.00 V / 0.010 A...", flush=True)
            await timed(device.set_current(PARK_CURRENT_A))
            await timed(device.set_voltage(PARK_VOLTAGE_V))

            await asyncio.sleep(1)
            state = await timed(device.get_all())

            print(
                f"Final voltage readback: {state.output_voltage:.4f} V",
                flush=True,
            )
            print(
                f"Final setpoints: {state.set_voltage:.4f} V / "
                f"{state.set_current:.4f} A",
                flush=True,
            )

        except Exception as exc:
            print(
                f"Cleanup warning: {type(exc).__name__}: {exc}",
                flush=True,
            )
            print(
                "Switch output OFF manually and check stored settings. "
                "Disconnect input power if necessary.",
                flush=True,
            )

        finally:
            try:
                await timed(device.disconnect())
                print("Connection closed.", flush=True)
            except Exception as exc:
                print(f"Disconnect warning: {exc}", flush=True)

    if samples:
        average_voltage = sum(s[0] for s in samples) / len(samples)
        average_current = sum(s[1] for s in samples) / len(samples)
        cc_count = sum(s[2] == "CC" for s in samples)

        print("\nReadback summary, excluding the first 2 seconds:")
        print(f"Average voltage:       {average_voltage:.4f} V")
        print(f"Average current:       {average_current * 1000:.2f} mA")
        print(
            f"Current from V / 470 Ω: "
            f"{average_voltage / LOAD_RESISTANCE_OHM * 1000:.2f} mA"
        )
        print(f"CC-mode samples:       {cc_count}/{len(samples)}")
        print("Compare these results with your independent DMM reading.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted. Confirm output OFF on the front panel.")
    except Exception as exc:
        print(f"\nTest error: {type(exc).__name__}: {exc}")
        print("Confirm output OFF on the front panel.")
        raise