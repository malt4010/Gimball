import asyncio
import pygame
from bleak import BleakClient, BleakScanner

TARGET_NAME = "Crane"
WRITE_UUID = None
NOTIFY_UUID = None
client = None

# Zhiyun BLE Protocol (reverse-engineered from packet captures)
#
# Each update sends 3 commands (7 bytes each) as a group:
#   Cmd1: 06 10 01 <4-byte tilt position>   (tilt axis)
#   Cmd2: 06 10 02 08 00 31 EB              (roll axis, always neutral)
#   Cmd3: 06 10 03 <4-byte pan position>    (pan axis)
#
# Position is a 32-bit value:
#   0x00000000 = max negative (down / left)
#   0x08000000 = center / neutral
#   0x0FFFFFFF = max positive (up / right)

TARGET_WRITE_UUID = "d44bc439-abfd-45a2-b575-925416129600"
TARGET_NOTIFY_UUID = "d44bc439-abfd-45a2-b575-925416129601"

CMD2_FIXED = bytearray.fromhex("061002080031EB")  # roll always neutral

# Known good command bytes from BLE captures (full 7-byte commands)
# Format: (cmd1_hex, cmd3_hex) for each axis combination
RAW = {
    # Neutral
    "neutral": ("061001080068BB", "061003080006DB"),
    # Tilt up (steady state from tiltup.txt)
    "tilt_up": ("0610010FFFEFDC", "061003080CC757"),
    # Tilt down (steady state from tiltdown.txt)
    "tilt_down": ("0610010003D171", "0610030793A53F"),
    # Pan left (steady state from panleft.txt)
    "pan_left": ("061001079D2A91", "0610030003BF11"),
    # Pan right (steady state from panright.txt)
    "pan_right": ("061001082D9D74", "0610030FFF81BC"),
}

# Parse raw hex into byte arrays for interpolation
def _parse(hex_str):
    return bytearray.fromhex(hex_str)

CMDS = {k: (_parse(v[0]), _parse(v[1])) for k, v in RAW.items()}


def lerp_bytes(a, b, t):
    """Linearly interpolate between two bytearrays (same length). t=0..1"""
    result = bytearray(len(a))
    # Treat bytes 3-6 as a 32-bit big-endian integer for smooth interpolation
    result[0:3] = a[0:3]  # header bytes stay same
    val_a = int.from_bytes(a[3:7], "big")
    val_b = int.from_bytes(b[3:7], "big")
    val = int(val_a + t * (val_b - val_a))
    result[3:7] = val.to_bytes(4, "big")
    return bytes(result)


def build_joystick_cmds(tilt_val, pan_val):
    """Build cmd1 and cmd3 from joystick values (-1..1).

    Interpolates between known-good capture bytes.
    """
    neutral_cmd1, neutral_cmd3 = CMDS["neutral"]

    # Tilt: interpolate cmd1 between neutral and up/down
    if tilt_val >= 0:
        cmd1 = lerp_bytes(neutral_cmd1, CMDS["tilt_up"][0], tilt_val)
    else:
        cmd1 = lerp_bytes(neutral_cmd1, CMDS["tilt_down"][0], -tilt_val)

    # Pan: interpolate cmd3 between neutral and left/right
    if pan_val >= 0:
        cmd3 = lerp_bytes(neutral_cmd3, CMDS["pan_right"][1], pan_val)
    else:
        cmd3 = lerp_bytes(neutral_cmd3, CMDS["pan_left"][1], -pan_val)

    return cmd1, cmd3


async def connect():
    global client, WRITE_UUID, NOTIFY_UUID

    print("Scanning...")
    devices = await BleakScanner.discover()

    target = None
    for d in devices:
        if d.name and TARGET_NAME in d.name:
            target = d
            break

    if not target:
        print("Ingen gimbal fundet")
        return False

    print(f"Connecting to {target.name}")
    client = BleakClient(target.address)
    await client.connect()

    for s in client.services:
        for c in s.characteristics:
            if c.uuid == TARGET_WRITE_UUID:
                WRITE_UUID = c.uuid
            if c.uuid == TARGET_NOTIFY_UUID:
                NOTIFY_UUID = c.uuid

    if not WRITE_UUID:
        for s in client.services:
            for c in s.characteristics:
                if "write-without-response" in c.properties and "write" in c.properties:
                    WRITE_UUID = c.uuid
                    break

    if not WRITE_UUID:
        print("Kunne ikke finde write characteristic!")
        return False

    if NOTIFY_UUID:
        try:
            await client.start_notify(NOTIFY_UUID, lambda s, d: None)
        except Exception as e:
            print(f"Notify fejl: {e}")

    # Send init (all neutral - exact bytes from capture)
    await send_raw("061001080068BB", "061002080031EB", "061003080006DB")
    print(f"Connected! Write={WRITE_UUID}")
    return True


async def send_axes(tilt_pos, pan_pos):
    """Send 3-command group with tilt and pan positions."""
    if not client or not WRITE_UUID:
        return
    cmd1 = build_cmd(0x01, tilt_pos)
    cmd2 = bytes(CMD2_FIXED)
    cmd3 = build_cmd(0x03, pan_pos)
    await client.write_gatt_char(WRITE_UUID, cmd1, response=False)
    await client.write_gatt_char(WRITE_UUID, cmd2, response=False)
    await client.write_gatt_char(WRITE_UUID, cmd3, response=False)


async def send_raw(cmd1_hex, cmd2_hex, cmd3_hex):
    """Send exact raw hex commands (for testing with capture data)."""
    if not client or not WRITE_UUID:
        return
    await client.write_gatt_char(WRITE_UUID, bytes.fromhex(cmd1_hex), response=False)
    await client.write_gatt_char(WRITE_UUID, bytes.fromhex(cmd2_hex), response=False)
    await client.write_gatt_char(WRITE_UUID, bytes.fromhex(cmd3_hex), response=False)


# ---------- GUI ----------
pygame.init()
screen = pygame.display.set_mode((500, 500))
pygame.display.set_caption("Zhiyun Crane Controller")
font = pygame.font.SysFont(None, 24)

joystick_center = (300, 300)
radius = 120


async def main():
    global client

    running = True
    connected = False

    while running:
        screen.fill((30, 30, 30))

        # Status
        status = "CONNECTED" if connected else "DISCONNECTED"
        color = (0, 200, 0) if connected else (200, 0, 0)
        screen.blit(font.render(status, True, color), (20, 20))

        # Buttons
        btn_connect = pygame.Rect(20, 50, 120, 40)
        pygame.draw.rect(screen, (0, 120, 80) if connected else (80, 80, 80), btn_connect)
        screen.blit(font.render("Connect", True, (255, 255, 255)), (35, 60))

        # Test buttons - exact bytes from BLE captures
        btn_pan_left = pygame.Rect(20, 110, 120, 40)
        pygame.draw.rect(screen, (80, 60, 60), btn_pan_left)
        screen.blit(font.render("Pan Left", True, (255, 255, 255)), (30, 120))

        btn_pan_right = pygame.Rect(20, 160, 120, 40)
        pygame.draw.rect(screen, (60, 60, 80), btn_pan_right)
        screen.blit(font.render("Pan Right", True, (255, 255, 255)), (30, 170))

        btn_neutral = pygame.Rect(20, 210, 120, 40)
        pygame.draw.rect(screen, (60, 80, 60), btn_neutral)
        screen.blit(font.render("Neutral", True, (255, 255, 255)), (35, 220))

        # Joystick
        pygame.draw.circle(screen, (60, 60, 60), joystick_center, radius, 2)
        pygame.draw.line(screen, (40, 40, 40), (joystick_center[0] - radius, joystick_center[1]),
                         (joystick_center[0] + radius, joystick_center[1]), 1)
        pygame.draw.line(screen, (40, 40, 40), (joystick_center[0], joystick_center[1] - radius),
                         (joystick_center[0], joystick_center[1] + radius), 1)

        # Labels
        screen.blit(font.render("Tilt Up", True, (150, 150, 150)),
                     (joystick_center[0] - 25, joystick_center[1] - radius - 25))
        screen.blit(font.render("Tilt Down", True, (150, 150, 150)),
                     (joystick_center[0] - 35, joystick_center[1] + radius + 10))
        screen.blit(font.render("Pan L", True, (150, 150, 150)),
                     (joystick_center[0] - radius - 50, joystick_center[1] - 10))
        screen.blit(font.render("Pan R", True, (150, 150, 150)),
                     (joystick_center[0] + radius + 10, joystick_center[1] - 10))

        mouse = pygame.mouse.get_pos()
        pressed = pygame.mouse.get_pressed()[0]

        tilt_val = 0.0
        pan_val = 0.0

        if pressed and connected:
            dx = mouse[0] - joystick_center[0]
            dy = mouse[1] - joystick_center[1]
            dx = max(-radius, min(radius, dx))
            dy = max(-radius, min(radius, dy))

            pygame.draw.circle(screen, (0, 200, 255),
                               (joystick_center[0] + dx, joystick_center[1] + dy), 12)

            pan_val = dx / radius     # -1 (left) to 1 (right)
            tilt_val = -dy / radius   # -1 (down) to 1 (up)

            cmd1, cmd3 = build_joystick_cmds(tilt_val, pan_val)
            cmd2 = bytes(CMD2_FIXED)
            await client.write_gatt_char(WRITE_UUID, cmd1, response=False)
            await client.write_gatt_char(WRITE_UUID, cmd2, response=False)
            await client.write_gatt_char(WRITE_UUID, cmd3, response=False)

        # Debug info
        screen.blit(font.render(f"Tilt: {tilt_val:.2f}  Pan: {pan_val:.2f}" if pressed and connected else "Idle",
                                True, (200, 200, 200)), (20, 460))

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            if event.type == pygame.MOUSEBUTTONDOWN:
                if btn_connect.collidepoint(event.pos):
                    connected = await connect()
                if connected:
                    if btn_pan_left.collidepoint(event.pos):
                        # Exact bytes from panleft.txt capture (steady state)
                        print("TEST: Pan Left (raw capture bytes)")
                        await send_raw(
                            "061001079D2A91",  # cmd1 from panleft capture
                            "061002080031EB",  # cmd2 always same
                            "0610030003BF11",  # cmd3 pan left (0x0003BF11)
                        )
                    if btn_pan_right.collidepoint(event.pos):
                        # Exact bytes from panright.txt capture (steady state)
                        print("TEST: Pan Right (raw capture bytes)")
                        await send_raw(
                            "061001082D9D74",  # cmd1 from panright capture
                            "061002080031EB",  # cmd2 always same
                            "0610030FFF81BC",  # cmd3 pan right (0x0FFF81BC)
                        )
                    if btn_neutral.collidepoint(event.pos):
                        # Init/neutral from captures
                        print("TEST: Neutral (raw capture bytes)")
                        await send_raw(
                            "061001080068BB",  # tilt neutral
                            "061002080031EB",  # roll neutral
                            "061003080006DB",  # pan neutral
                        )

        pygame.display.flip()
        await asyncio.sleep(0.02)

    if client:
        await client.disconnect()


asyncio.run(main())
