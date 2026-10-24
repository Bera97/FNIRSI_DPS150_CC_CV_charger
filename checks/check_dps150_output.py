import asyncio
import math

from dps150 import DPS150


PORT = "COM3"
VOLTAGE_V = 2.00
CURRENT_LIMIT_A = 0.010
COMMAND_TIMEOUT_S = 3.0
TEST_DURATION_S = 10.0


async def timed(awaitable):
    return await asyncio.wait_for(
        awaitable,
        timeout=COMMAND_TIMEOUT_S,
    )


async def main():
    device = DPS150(port=PORT)

    try:
        print(f"Connecting to {PORT}...", flush=True)
        await timed(device.connect())

        print("Commanding output OFF...", flush=True)
        await timed(device.disable_output())

        print("Setting 2.00 V / 0.010 A...", flush=True)
        await timed(device.set_current(CURRENT_LIMIT_A))
        await timed(device.set_voltage(VOLTAGE_V))
        await asyncio.sleep(0.5)

        state = await timed(device.get_all())

        settings_ok = (
            math.isclose(
                state.set_voltage,
                VOLTAGE_V,
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
            raise RuntimeError("Setpoint verification failed; output not enabled.")

        if state.protection_state.name != "NORMAL":
            raise RuntimeError(
                f"Protection is not normal: {state.protection_state}"
            )

        print("\nSetpoints verified.", flush=True)
        print(
            "Output will turn ON after a 3-second countdown.",
            flush=True,
        )
        print(
            "Watch the DMM. Expected voltage: approximately 2.00 V.",
            flush=True,
        )

        for remaining in (3, 2, 1):
            print(f"{remaining}...", flush=True)
            await asyncio.sleep(1)

        print("\nEnabling output...", flush=True)
        await timed(device.enable_output())

        loop = asyncio.get_running_loop()
        started = loop.time()
        deadline = started + TEST_DURATION_S

        while loop.time() < deadline:
            remaining = deadline - loop.time()

            state = await asyncio.wait_for(
                device.get_all(),
                timeout=min(COMMAND_TIMEOUT_S, remaining),
            )

            print(
                f"t={loop.time() - started:5.1f} s | "
                f"V={state.output_voltage:.4f} V | "
                f"I={state.output_current * 1000:.2f} mA | "
                f"Mode={state.mode} | "
                f"Protection={state.protection_state}",
                flush=True,
            )

            if state.protection_state.name != "NORMAL":
                raise RuntimeError(
                    f"Protection triggered: {state.protection_state}"
                )

            if not math.isfinite(state.output_voltage):
                raise RuntimeError("Invalid voltage readback.")

            if state.output_voltage > 2.20:
                raise RuntimeError("Unexpected voltage above 2.20 V.")

            await asyncio.sleep(
                max(0.0, min(1.0, deadline - loop.time()))
            )

    finally:
        print("\nCleanup: commanding output OFF...", flush=True)

        try:
            await timed(device.disable_output())
            print("Output-OFF command returned.", flush=True)

            await asyncio.sleep(1)
            state = await timed(device.get_all())
            print(
                f"Final voltage readback: "
                f"{state.output_voltage:.4f} V",
                flush=True,
            )

        except Exception as exc:
            print(
                f"Cleanup warning: {type(exc).__name__}: {exc}",
                flush=True,
            )
            print(
                "Switch the output OFF manually. "
                "If necessary, disconnect its input power.",
                flush=True,
            )

        finally:
            try:
                await timed(device.disconnect())
                print("Connection closed.", flush=True)
            except Exception as exc:
                print(f"Disconnect warning: {exc}", flush=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted. Verify output OFF on the front panel.")
    except Exception as exc:
        print(f"\nTest error: {type(exc).__name__}: {exc}")
        print("Verify output OFF on the front panel.")
        raise