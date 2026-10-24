import asyncio

from dps150_adapter import FreshDPS150


PORT = "COM3"
COMMAND_TIMEOUT_S = 3.0


async def bounded(awaitable):
    return await asyncio.wait_for(
        awaitable,
        timeout=COMMAND_TIMEOUT_S,
    )


async def main():
    device = FreshDPS150(port=PORT)
    previous_received_at = None

    try:
        print(f"Connecting to {PORT}...", flush=True)
        await asyncio.wait_for(device.connect(), timeout=10.0)

        print("Commanding output OFF...", flush=True)
        await bounded(device.disable_output())

        print("\nReading five complete-state responses...", flush=True)

        for index in range(1, 6):
            sample = await device.read_fresh(
                timeout_s=COMMAND_TIMEOUT_S
            )

            if sample.output_enabled:
                raise RuntimeError(
                    "Device reports output ON after the output-OFF command."
                )

            if (
                previous_received_at is not None
                and sample.received_at_s <= previous_received_at
            ):
                raise RuntimeError(
                    "Response timestamps did not advance."
                )

            previous_received_at = sample.received_at_s

            print(
                f"{index}/5 | "
                f"received={sample.received_at_s:.3f} s | "
                f"Vin={sample.input_voltage_V:.3f} V | "
                f"Vout={sample.output_voltage_V:.4f} V | "
                f"Iout={sample.output_current_A * 1000:.2f} mA | "
                f"output_on={sample.output_enabled} | "
                f"mode={sample.mode} | "
                f"protection={sample.protection}",
                flush=True,
            )

            await asyncio.sleep(1.0)

        print(
            "\nFresh-response diagnostic passed. "
            "Output was never commanded ON.",
            flush=True,
        )

    finally:
        print("\nCleanup: attempting output OFF...", flush=True)

        try:
            await bounded(device.disable_output())
            print(
                "Output-OFF command sent; check the front panel.",
                flush=True,
            )
        except Exception as exc:
            print(
                f"Output-OFF warning: {type(exc).__name__}: {exc}",
                flush=True,
            )
            print("Switch output OFF manually.", flush=True)

        finally:
            try:
                await bounded(device.disconnect())
                print("Connection closed.", flush=True)
            except Exception as exc:
                print(f"Disconnect warning: {exc}", flush=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted. Confirm output OFF on the front panel.")
    except Exception as exc:
        print(f"\nDiagnostic failed: {type(exc).__name__}: {exc}")
        print("Confirm output OFF on the front panel.")
        raise