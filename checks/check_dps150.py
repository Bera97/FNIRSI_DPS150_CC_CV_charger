import asyncio

from dps150 import DPS150


PORT = "COM3"
TIMEOUT_S = 10.0


async def timed(awaitable):
    return await asyncio.wait_for(awaitable, timeout=TIMEOUT_S)


async def main():
    device = DPS150(port=PORT)

    try:
        print(f"Connecting to {PORT}...", flush=True)
        await timed(device.connect())
        print("Connection opened.", flush=True)

        print("Sending output-OFF command...", flush=True)
        await timed(device.disable_output())
        print(
            "Output-OFF command returned. Check the front-panel indicator.",
            flush=True,
        )

        print("\nRequesting device information...", flush=True)
        info = await timed(device.get_info())

        print(f"Model:             {info.model_name}")
        print(f"Firmware version:  {info.firmware_version}")
        print(f"Hardware version:  {info.hardware_version}")

        print("\nRequesting measurements and settings...", flush=True)
        state = await timed(device.get_all())

        print(f"Measured voltage:  {state.output_voltage:.4f} V")
        print(f"Measured current:  {state.output_current:.4f} A")
        print(f"Measured power:    {state.output_power:.4f} W")
        print(f"Device temperature:{state.temperature: .2f} C")
        print(f"Voltage setpoint:  {state.set_voltage:.4f} V")
        print(f"Current setpoint:  {state.set_current:.4f} A")
        print(f"Mode:              {state.mode}")
        print(f"Protection state:  {state.protection_state}")

        print("\nReadback diagnostic completed.", flush=True)

    finally:
        print("\nCleanup: attempting output OFF...", flush=True)
        try:
            await timed(device.disable_output())
            print("Cleanup output-OFF command returned.", flush=True)
        except Exception as exc:
            print(
                f"Could not confirm cleanup command: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            print("Check and switch off the output manually.", flush=True)

        print("Closing connection...", flush=True)
        try:
            await timed(device.disconnect())
            print("Connection closed.", flush=True)
        except Exception as exc:
            print(
                f"Disconnect warning: {type(exc).__name__}: {exc}",
                flush=True,
            )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted. Confirm the output is OFF on the front panel.")
    except Exception as exc:
        print(f"\nDiagnostic failed: {type(exc).__name__}: {exc}")
        print("Confirm the output is OFF on the front panel.")
        raise