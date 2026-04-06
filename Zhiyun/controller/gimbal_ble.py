"""
Zhiyun Crane BLE Gimbal Controller

Importable module for connecting to and controlling a Zhiyun Crane gimbal
via Bluetooth Low Energy. Reverse-engineered protocol - see PROTOCOL.md.
"""
import asyncio
from bleak import BleakClient, BleakScanner


# BLE UUIDs from service discovery
_TARGET_WRITE_UUID = "d44bc439-abfd-45a2-b575-925416129600"
_TARGET_NOTIFY_UUID = "d44bc439-abfd-45a2-b575-925416129601"

# Roll axis is always neutral
_CMD2_FIXED = bytes.fromhex("061002080031EB")

# Known-good steady-state bytes from BLE captures
_RAW = {
    "neutral":   ("061001080068BB", "061003080006DB"),
    "tilt_up":   ("0610010FFFEFDC", "061003080CC757"),
    "tilt_down": ("0610010003D171", "0610030793A53F"),
    "pan_left":  ("061001079D2A91", "0610030003BF11"),
    "pan_right": ("061001082D9D74", "0610030FFF81BC"),
}
_CMDS = {k: (bytearray.fromhex(v[0]), bytearray.fromhex(v[1])) for k, v in _RAW.items()}


def _lerp_bytes(a, b, t):
    """Interpolate bytes 3-6 as a 32-bit big-endian value. t in 0..1."""
    result = bytearray(len(a))
    result[0:3] = a[0:3]
    val_a = int.from_bytes(a[3:7], "big")
    val_b = int.from_bytes(b[3:7], "big")
    result[3:7] = int(val_a + t * (val_b - val_a)).to_bytes(4, "big")
    return bytes(result)


class ZhiyunGimbal:
    """Async controller for Zhiyun Crane gimbal over BLE."""

    def __init__(self, device_name="Crane"):
        self.device_name = device_name
        self._client = None
        self._write_uuid = None
        self._connected = False

    @property
    def connected(self):
        return self._connected and self._client and self._client.is_connected

    async def connect(self):
        """Scan for and connect to the gimbal. Returns True on success."""
        devices = await BleakScanner.discover()
        target = None
        for d in devices:
            if d.name and self.device_name in d.name:
                target = d
                break

        if not target:
            return False

        self._client = BleakClient(target.address)
        await self._client.connect()

        # Find correct write characteristic
        for s in self._client.services:
            for c in s.characteristics:
                if c.uuid == _TARGET_WRITE_UUID:
                    self._write_uuid = c.uuid
                if c.uuid == _TARGET_NOTIFY_UUID:
                    try:
                        await self._client.start_notify(c.uuid, lambda s, d: None)
                    except Exception:
                        pass

        if not self._write_uuid:
            for s in self._client.services:
                for c in s.characteristics:
                    if "write-without-response" in c.properties and "write" in c.properties:
                        self._write_uuid = c.uuid
                        break

        if not self._write_uuid:
            return False

        # Init sequence from BLE captures
        # 1. Speed/mode setting (required for pan to work)
        await self._client.write_gatt_char(self._write_uuid,
            bytes.fromhex("060105000050C1"), response=False)
        await asyncio.sleep(0.1)
        # 2. Neutral axis init
        await self._send_raw("061001080068BB", "061002080031EB", "061003080006DB")
        self._connected = True
        return True

    async def disconnect(self):
        """Disconnect from gimbal."""
        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                pass
        self._connected = False

    async def move(self, tilt=0.0, pan=0.0):
        """Move gimbal. tilt and pan are -1.0 to 1.0 (0=neutral).

        tilt: positive=up, negative=down
        pan: positive=right, negative=left
        """
        if not self.connected:
            return

        tilt = max(-1.0, min(1.0, tilt))
        pan = max(-1.0, min(1.0, pan))

        cmd1, cmd3 = self._build_cmds(tilt, pan)
        await self._client.write_gatt_char(self._write_uuid, cmd1, response=False)
        await self._client.write_gatt_char(self._write_uuid, _CMD2_FIXED, response=False)
        await self._client.write_gatt_char(self._write_uuid, cmd3, response=False)

    async def stop(self):
        """Send neutral/stop command."""
        await self._send_raw("061001080068BB", "061002080031EB", "061003080006DB")

    async def move_raw(self, direction):
        """Send exact capture bytes for a direction.

        direction: 'tilt_up', 'tilt_down', 'pan_left', 'pan_right', 'neutral'
        """
        if not self.connected or direction not in _RAW:
            return
        cmd1_hex, cmd3_hex = _RAW[direction]
        await self._send_raw(cmd1_hex, "061002080031EB", cmd3_hex)

    def _build_cmds(self, tilt_val, pan_val):
        """Interpolate between known-good capture bytes."""
        neutral_cmd1, neutral_cmd3 = _CMDS["neutral"]

        if tilt_val >= 0:
            cmd1 = _lerp_bytes(neutral_cmd1, _CMDS["tilt_up"][0], tilt_val)
        else:
            cmd1 = _lerp_bytes(neutral_cmd1, _CMDS["tilt_down"][0], -tilt_val)

        if pan_val >= 0:
            cmd3 = _lerp_bytes(neutral_cmd3, _CMDS["pan_right"][1], pan_val)
        else:
            cmd3 = _lerp_bytes(neutral_cmd3, _CMDS["pan_left"][1], -pan_val)

        return cmd1, cmd3

    async def _send_raw(self, cmd1_hex, cmd2_hex, cmd3_hex):
        """Send exact hex byte commands."""
        if not self.connected:
            return
        c = self._client
        w = self._write_uuid
        await c.write_gatt_char(w, bytes.fromhex(cmd1_hex), response=False)
        await c.write_gatt_char(w, bytes.fromhex(cmd2_hex), response=False)
        await c.write_gatt_char(w, bytes.fromhex(cmd3_hex), response=False)
