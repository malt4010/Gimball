"""
DJI OSMO Mobile SE - Response Analysis
Check if gimbal responds, ignores, or returns error codes.
"""
import asyncio
import struct
from bleak import BleakClient, BleakScanner

TARGET_NAME = "OMSE"
client = None
write_uuid = None
all_notifications = []

# DJI DUML CRC-16
CRC16_TABLE = []
def _init_crc16():
    for i in range(256):
        crc = i
        for _ in range(8):
            crc = ((crc >> 1) ^ 0x8408) if (crc & 1) else (crc >> 1)
        CRC16_TABLE.append(crc & 0xFFFF)
_init_crc16()

def crc16(data):
    crc = 0x3692
    for b in data:
        crc = ((crc >> 8) & 0xFF) ^ CRC16_TABLE[(crc ^ b) & 0xFF]
    return crc & 0xFFFF

seq_counter = 0x0100

def build_duml(sender, receiver, flags, cmd_set, cmd_id, payload=b""):
    global seq_counter
    total_len = 13 + len(payload)
    b1 = total_len & 0xFF
    b2 = ((total_len >> 8) & 0x03) | (1 << 2)
    header = bytes([0x55, b1, b2])
    hcrc = crc16(header)
    pkt = bytearray(header)
    pkt += struct.pack("<H", hcrc)
    pkt.append(sender)
    pkt.append(receiver)
    pkt += struct.pack("<H", seq_counter)
    seq_counter = (seq_counter + 1) & 0xFFFF
    pkt.append(flags)
    pkt.append(cmd_set)
    pkt.append(cmd_id)
    pkt += payload
    pcrc = crc16(pkt)
    pkt += struct.pack("<H", pcrc)
    return bytes(pkt)


def parse_duml(data):
    """Parse a DUML packet and return dict."""
    if len(data) < 13 or data[0] != 0x55:
        return None
    length = data[1] | ((data[2] & 0x03) << 8)
    if len(data) < length:
        return None
    return {
        "length": length,
        "sender": data[5],
        "receiver": data[6],
        "seq": struct.unpack("<H", data[7:9])[0],
        "flags": data[9],
        "is_response": bool(data[9] & 0x80),
        "is_request": bool(data[9] & 0x40),
        "cmd_set": data[10],
        "cmd_id": data[11] if len(data) > 11 else 0,
        "payload": data[12:length-2] if length > 13 else b"",
        "raw": data[:length].hex(),
    }


def notification_handler(sender, data):
    all_notifications.append(data)


async def connect():
    global client, write_uuid
    print("Scanning...")
    devices = await BleakScanner.discover(timeout=10)
    target = None
    for d in devices:
        if d.name and TARGET_NAME in d.name:
            target = d
            break
    if not target:
        print("Not found!")
        return False

    print(f"Connecting to {target.name}...")
    client = BleakClient(target.address)
    await client.connect()

    for service in client.services:
        for char in service.characteristics:
            if "write-without-response" in char.properties and write_uuid is None:
                write_uuid = char.uuid
            if "notify" in char.properties:
                try:
                    await client.start_notify(char.uuid, notification_handler)
                except:
                    pass
    print(f"Connected. Write: {write_uuid}\n")
    return True


async def send_and_analyze(data, label, wait=1.0):
    """Send a command and analyze all responses received."""
    before = len(all_notifications)

    # Parse what we're sending
    sent = parse_duml(data)
    sent_seq = sent["seq"] if sent else "?"
    sent_set = sent["cmd_set"] if sent else "?"
    sent_id = sent["cmd_id"] if sent else "?"
    print(f"SEND: {label}")
    print(f"  seq=0x{sent_seq:04X} set=0x{sent_set:02X} id=0x{sent_id:02X} ({len(data)}B)")
    print(f"  hex: {data.hex()[:80]}")

    await client.write_gatt_char(write_uuid, data, response=False)
    await asyncio.sleep(wait)

    new_notifs = all_notifications[before:]
    responses = 0
    for n in new_notifs:
        parsed = parse_duml(n)
        if not parsed:
            continue
        if parsed["is_response"]:
            responses += 1
            p = parsed["payload"]
            # First byte of response payload is usually the return code
            retcode = p[0] if p else None
            retcode_str = {
                0x00: "SUCCESS",
                0x01: "FAILURE",
                0x02: "NOT_SUPPORTED",
                0x03: "TIMEOUT",
                0x04: "NOT_AUTHORIZED",
                0x05: "BUSY",
                0x06: "INVALID_PARAM",
                0x07: "NOT_READY",
                0xE0: "ENCRYPTED_REQUIRED",
                0xE1: "AUTH_REQUIRED",
                0xE2: "SESSION_INVALID",
                0xFF: "UNKNOWN_ERROR",
            }.get(retcode, f"CODE_0x{retcode:02X}" if retcode is not None else "EMPTY")

            print(f"  ** RESPONSE: set=0x{parsed['cmd_set']:02X} id=0x{parsed['cmd_id']:02X} "
                  f"retcode={retcode_str} payload={p.hex()}")

    if responses == 0:
        print(f"  -- No direct responses (got {len(new_notifs)} notifications, all unsolicited)")
    print()


async def main():
    if not await connect():
        return

    # Wait for initial notifications
    await asyncio.sleep(2)
    print(f"Baseline: {len(all_notifications)} notifications in 2 sec\n")
    print("=" * 70)

    # Test 1: Speed control (CmdSet 0x04, ID 0x00)
    payload = struct.pack("<hhh", 0, 500, 0)
    pkt = build_duml(0x02, 0x04, 0x40, 0x04, 0x00, payload)
    await send_and_analyze(pkt, "Gimbal Speed (set=0x04 id=0x00)")

    # Test 2: Angle control (CmdSet 0x04, ID 0x01)
    payload = struct.pack("<hhh", 0, 1000, 0)
    pkt = build_duml(0x02, 0x04, 0x40, 0x04, 0x01, payload)
    await send_and_analyze(pkt, "Gimbal Angle (set=0x04 id=0x01)")

    # Test 3: Tracking (CmdSet 0x23, ID 0x09) - the captured command set
    pkt = build_duml(0x02, 0x04, 0x40, 0x23, 0x09, bytes(36))
    await send_and_analyze(pkt, "Tracking cmd (set=0x23 id=0x09)")

    # Test 4: Raw captured packet
    raw = bytes.fromhex(
        "5531045302045bd3402309beb4e3c6bc4e06000005d002020401000200000"
        "0c0bb003f01d0023f7ab16a3d8ebe6c3d66fe"
    )
    await send_and_analyze(raw, "Raw captured tiltup packet")

    # Test 5: Try querying gimbal info (usually unauthenticated)
    pkt = build_duml(0x02, 0x04, 0x40, 0x00, 0x01, b"")  # Get version
    await send_and_analyze(pkt, "Get version (set=0x00 id=0x01)")

    pkt = build_duml(0x02, 0x04, 0x40, 0x00, 0x05, b"")  # Get device info
    await send_and_analyze(pkt, "Get device info (set=0x00 id=0x05)")

    # Test 6: Send with different sender IDs
    for sender in [0x00, 0x02, 0x04, 0x06, 0x08]:
        payload = struct.pack("<hhh", 0, 500, 0)
        pkt = build_duml(sender, 0x04, 0x40, 0x04, 0x00, payload)
        await send_and_analyze(pkt, f"Speed with sender=0x{sender:02X}", wait=0.5)

    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)

    # Count response types
    response_codes = {}
    for n in all_notifications:
        p = parse_duml(n)
        if p and p["is_response"] and p["payload"]:
            code = p["payload"][0]
            key = f"0x{code:02X}"
            response_codes[key] = response_codes.get(key, 0) + 1

    if response_codes:
        print(f"Response codes seen: {response_codes}")
    else:
        print("NO response packets received at all.")
        print("The gimbal sends telemetry but does not respond to our commands.")
        print("This suggests our packets are being silently dropped,")
        print("likely due to authentication or encryption requirements.")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
