import asyncio
import math
import struct
from dataclasses import dataclass

from dps150 import DPS150
from dps150.constants import ALL, CMD_GET, PROTECTION_STATES


@dataclass(frozen=True)
class FreshState:
    received_at_s: float
    input_voltage_V: float
    set_voltage_V: float
    set_current_A: float
    output_voltage_V: float
    output_current_A: float
    output_power_W: float
    device_temperature_C: float
    output_enabled: bool
    mode: str
    protection: str
    protection_code: int


def decode_complete_state(data, received_at_s):
    if len(data) < 139:
        raise ValueError(
            f"Complete-state packet is too short: {len(data)} bytes."
        )

    values = struct.unpack_from("<7f", data, 0)

    if not all(math.isfinite(value) for value in values):
        raise ValueError("Complete-state packet contains nonfinite values.")

    output_flag = data[107]
    protection_index = data[108]
    mode_flag = data[109]

    if output_flag not in (0, 1):
        raise ValueError(f"Invalid output-enable value: {output_flag}")

    if mode_flag not in (0, 1):
        raise ValueError(f"Invalid mode value: {mode_flag}")

    if not 0 <= protection_index < len(PROTECTION_STATES):
        raise ValueError(
            f"Invalid protection-state index: {protection_index}"
        )

    protection = (
        "NORMAL"
        if protection_index == 0
        else str(PROTECTION_STATES[protection_index])
        or f"FAULT_{protection_index}"
    )

    return FreshState(
        received_at_s=received_at_s,
        input_voltage_V=values[0],
        set_voltage_V=values[1],
        set_current_A=values[2],
        output_voltage_V=values[3],
        output_current_A=values[4],
        output_power_W=values[5],
        device_temperature_C=values[6],
        output_enabled=bool(output_flag),
        mode="CC" if mode_flag == 0 else "CV",
        protection=protection,
        protection_code=protection_index,
    )


class FreshDPS150(DPS150):
    """Wait for received complete-state packets instead of cached state."""

    def __init__(self, port="COM3"):
        super().__init__(port=port)
        self._request_lock = asyncio.Lock()
        self._pending_response = None
        self._acquisition_failed = False

    def _on_packet_received(self, command, type_code, data):
        super()._on_packet_received(command, type_code, data)

        pending = self._pending_response

        if type_code != ALL or pending is None or pending.done():
            return

        try:
            snapshot = decode_complete_state(
                data,
                asyncio.get_running_loop().time(),
            )
        except Exception as exc:
            pending.set_exception(exc)
        else:
            pending.set_result(snapshot)

    async def read_fresh(self, timeout_s=3.0):
        if (
            isinstance(timeout_s, bool)
            or not isinstance(timeout_s, (int, float))
            or not math.isfinite(timeout_s)
            or timeout_s <= 0
        ):
            raise ValueError("timeout_s must be finite and positive.")

        async with self._request_lock:
            if self._acquisition_failed:
                raise RuntimeError(
                    "Acquisition previously failed. "
                    "Disconnect and create a new device instance."
                )

            if self._polling_task and not self._polling_task.done():
                raise RuntimeError(
                    "Background polling must not run during fresh acquisition."
                )

            loop = asyncio.get_running_loop()
            pending = loop.create_future()
            self._pending_response = pending

            async def request_and_wait():
                await self._send_command(CMD_GET, ALL, b"")
                return await pending

            try:
                return await asyncio.wait_for(
                    request_and_wait(),
                    timeout=float(timeout_s),
                )
            except BaseException:
                self._acquisition_failed = True
                raise
            finally:
                self._pending_response = None

                if not pending.done():
                    pending.cancel()