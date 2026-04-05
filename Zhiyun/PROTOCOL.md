# Zhiyun Crane BLE Protocol Documentation

Reverse-engineered from Bluetooth HCI packet captures between the official Zhiyun app (iOS) and a Zhiyun Crane gimbal.

**Device:** Crane84D9 (MAC: AC:9A:22:88:08:BD)  
**Firmware:** 1.2.1  
**Manufacturer:** Zhiyun (hex: `5A686979756E`)  
**System ID:** `BD0888229AAC0000`

---

## 1. BLE Service & Characteristic Map

After GATT discovery, these are the relevant characteristics:

| Handle | UUID | Properties | Purpose |
|--------|------|-----------|---------|
| 0x002C | `d44bc439-abfd-45a2-b575-925416129600` | write, write-without-response | **Command write** (all commands go here) |
| 0x002F | `d44bc439-abfd-45a2-b575-925416129601` | notify | **Response notifications** (gimbal echoes/responds) |
| 0x0032 | `d44bc439-abfd-45a2-b575-925416129610` | notify | Secondary notification channel |

**Important:** There is also a characteristic at handle 0x0028 (`013784cf-...`) with write-without-response, but this is NOT the correct one for gimbal control. Always use handle 0x002C / UUID `...9600`.

All commands are sent as **Write Command** (write-without-response, ATT opcode 0x52).

Before sending commands, enable notifications on both `...9601` (handle 0x002F) and `...9610` (handle 0x0032).

---

## 2. Command Format

All commands start with byte `0x06` (likely packet length indicator = 6 remaining bytes for 7-byte commands).

### Two Protocol Families

| Prefix | Name | Purpose |
|--------|------|---------|
| `06 01 XX` | Register/Command | Initialization, configuration, keepalive |
| `06 81 XX` | Config Write | Calibration/PID parameters during init |
| `06 10 XX` | Axis Control | Real-time tilt/pan/roll movement |

---

## 3. Initialization Sequence (from connect.txt)

After GATT discovery and enabling notifications, the Zhiyun app sends this exact sequence before any movement is possible:

```
# Step 1: Speed/sensitivity init (sent 3 times)
0601 0500 0050 C1
0601 0500 0050 C1
0601 0500 0050 C1

# Step 2: Parameter setting
0601 0200 00D5 51

# Step 3: Configuration writes (PID/calibration - 0681 prefix)
0681 5E1F 4078 4F
0681 6409 C402 F2 | 06 8161 0064 E670    (13 bytes, two commands packed)
0681 5B00 C890 72 | 06 815F 1F40 4F7F    (13 bytes)
0681 6509 C435 C2 | 06 8162 0064 BF20    (13 bytes)
0681 5C01 F4D1 0C | 06 8160 1770 1911    (13 bytes)
0681 6611 94BC BD | 06 8163 0064 8810    (13 bytes)
0681 5D01 F4E6 3C | 06 8167 0000 78F2    (13 bytes)

# Step 4: Additional register settings
0601 0400 0067 F1
0601 7C00 0016 58
0601 7D00 0021 68 | 06 017E 0000 7838    (13 bytes)
0601 7F00 004F 08 | 06 0125 0000 D607    (13 bytes)
0601 2600 008F 57 | 06 0168 0000 89FB    (13 bytes)
0601 6900 00BE CB

# Step 5: Final "start" command (~3 sec after step 4)
0601 0600 0009 91
```

**Note:** The 13-byte writes contain two 7-byte and 6-byte commands packed together (the `|` shows the boundary). The gimbal echoes each command back via notifications.

### Notification Responses During Init

The gimbal responds to each command via Handle 0x002F. Responses often differ from sent values in bytes 4-5, indicating the gimbal returns its current state:

- Sent: `0601 0500 0050 C1` -> Received: `0601 0500 08D1 C9`
- Sent: `0601 0400 0067 F1` -> Received: `0601 0400 AA73 51`
- Sent: `0601 0600 0009 91` -> Received: `0601 0602 BD19 85`

---

## 4. Axis Control Protocol (Movement)

After initialization, movement is controlled via `0610` commands sent in **groups of 3** (one per axis):

```
Cmd1: 06 10 01 <4 bytes>    Tilt axis (pitch)
Cmd2: 06 10 02 <4 bytes>    Roll axis
Cmd3: 06 10 03 <4 bytes>    Pan axis (yaw)
```

### 4.1 Command Structure

Each command is 7 bytes:
```
06 10 AA BB CC DD EE
 |  |  |  |--------|
 |  |  |  4-byte position/speed value (big-endian)
 |  |  Axis: 01=tilt, 02=roll, 03=pan
 |  Protocol ID
 Length/header
```

### 4.2 The 4-Byte Value

Bytes 3-6 form a 32-bit value that encodes speed/direction:

| Value | Meaning |
|-------|---------|
| `0x00000000` | Maximum negative (tilt down / pan left) |
| `0x08000000` | Neutral / center / stop |
| `0x0FFFFFFF` | Maximum positive (tilt up / pan right) |

The value ramps smoothly. The Zhiyun app transitions from neutral to max over ~600ms (3 update cycles at 200ms intervals).

### 4.3 Neutral/Init Values

These exact bytes are sent at the start of every movement recording:

```
Cmd1 (tilt):  06 10 01 08 00 68 BB    (0x080068BB ~ neutral)
Cmd2 (roll):  06 10 02 08 00 31 EB    (0x080031EB ~ neutral, ALWAYS this value)
Cmd3 (pan):   06 10 03 08 00 06 DB    (0x080006DB ~ neutral)
```

**Roll axis (Cmd2) is ALWAYS `061002080031EB`** in all captures. It never changes.

### 4.4 Movement Examples

**Tilt Up** (cmd1 progression, cmd3 stays ~neutral):
```
06 10 01 08 00 68 BB    -> 0x080068BB (neutral)
06 10 01 0F FC DF BF    -> 0x0FFCDFBF (ramping up)
06 10 01 0F FE FF FD    -> 0x0FFEFFFD (nearly max)
06 10 01 0F FF EF DC    -> 0x0FFFEFDC (steady state max up)
```

**Tilt Down** (cmd1 progression):
```
06 10 01 08 00 68 BB    -> 0x080068BB (neutral)
06 10 01 02 0E 66 BE    -> 0x020E66BE (ramping down)
06 10 01 00 37 A7 A6    -> 0x0037A7A6 (fast down)
06 10 01 00 04 A1 96    -> 0x0004A196 (near max down)
06 10 01 00 03 D1 71    -> 0x0003D171 (steady state max down)
```

**Pan Left** (cmd3 progression, cmd1 stays ~neutral):
```
06 10 03 08 00 06 DB    -> 0x080006DB (neutral)
06 10 03 00 0E 6E BC    -> 0x000E6EBC (moving left)
06 10 03 00 01 9F 53    -> 0x00019F53 (faster left)
06 10 03 00 03 BF 11    -> 0x0003BF11 (steady state max left)
```

**Pan Right** (cmd3 progression):
```
06 10 03 08 00 06 DB    -> 0x080006DB (neutral)
06 10 03 0F F6 10 95    -> 0x0FF61095 (moving right)
06 10 03 0F FF 81 BC    -> 0x0FFF81BC (steady state max right)
```

### 4.5 Timing

- Commands are sent in groups of 3 (cmd1 + cmd2 + cmd3) every **~200ms** (5 Hz)
- All 3 commands in a group are sent within the same millisecond
- The gimbal echoes each group back via notifications
- During tilt-only movement, the pan axis cmd3 stays near neutral but bytes 4-6 still fluctuate slightly

### 4.6 Cross-Axis Behavior

When moving on one axis, the other axis values are NOT exactly `0x08000000`. They fluctuate around neutral:

- During tilt up, cmd3 values: `0x080006DB` -> `0x0873482F` -> `0x080CC757` (small fluctuations)
- During pan left, cmd1 values: `0x080068BB` -> `0x07171A53` -> `0x079D2A91` (slight drop)

This suggests the lower 3 bytes may encode real-time encoder/angle feedback that the app reads from notifications and echoes back. **For reliable operation, use the exact capture bytes rather than computing values.**

---

## 5. Keepalive / Heartbeat Protocol

When the gimbal is connected but no joystick input is active, the app sends keepalive packets (from changemodes.txt):

```
# Alternating pattern, ~1 second interval:
0601 2700 00B8 67    (type 01, register 0x27)
0681 2700 0175 7E    (type 81, register 0x27)

0601 2700 00B8 67    (always same)
0681 2700 0065 5F    (bytes 4-5 vary)

0601 2700 00B8 67
0681 2700 0245 1D
```

- The `0601 27` command is always `0601 2700 00B8 67`
- The `0681 27` command has varying bytes 4-5 (encoder position feedback?)
- Sent at ~1 second intervals
- This is completely separate from the `0610` axis control protocol

---

## 6. Special Command: Speed Setting

Found only in panleft.txt (first movement after init):

```
0601 0500 0050 C1
```

This is the same command from the initialization sequence (step 1). It may set the joystick sensitivity or movement speed. The value `0050` (decimal 80) could be a speed percentage or sensitivity parameter.

Also appears 3x during the connect.txt initialization.

---

## 7. Register Map (Partial)

Based on `06 01 XX` commands observed:

| Register (byte 2) | Purpose | Example |
|-------------------|---------|---------|
| `0x02` | Unknown parameter | `0601 0200 00D5 51` |
| `0x04` | Unknown parameter | `0601 0400 0067 F1` |
| `0x05` | Speed/sensitivity | `0601 0500 0050 C1` (value 0x0050=80) |
| `0x06` | Start/enable | `0601 0600 0009 91` |
| `0x25` | Config | `0601 2500 00D6 07` |
| `0x26` | Config | `0601 2600 008F 57` |
| `0x27` | Heartbeat/keepalive | `0601 2700 00B8 67` |
| `0x68` | Config | `0601 6800 0089 FB` |
| `0x69` | Config | `0601 6900 00BE CB` |
| `0x7C` | Config | `0601 7C00 0016 58` |
| `0x7D` | Config | `0601 7D00 0021 68` |
| `0x7E` | Config | `0601 7E00 0078 38` |
| `0x7F` | Config | `0601 7F00 004F 08` |

---

## 8. Bytes 4-6: Position Data vs Checksum

The last 3 bytes (or last byte alone) of each command could potentially include a checksum. However, analysis shows:

- Simple sum, XOR, two's complement, and CRC-8 do NOT match any common algorithm
- During steady-state movement, the last byte changes independently of other bytes
- The gimbal echoes commands back with different bytes 4-5 (suggesting position feedback)
- **The most reliable approach is to use exact bytes from captures** rather than computing values

If implementing variable speed, interpolate the full 4-byte value between known-good capture values.

---

## 9. Implementation Checklist

1. **Connect** via BLE, find characteristic UUID `d44bc439-abfd-45a2-b575-925416129600`
2. **Enable notifications** on `...9601` and `...9610`
3. **Send initialization sequence** (Section 3) - especially `0601 0500 0050 C1` (3x) and `0601 0600 0009 91`
4. **Send axis control** as groups of 3 commands (cmd1 + cmd2 + cmd3) at ~200ms intervals
5. **Use exact capture bytes** for reliable movement - interpolate between known neutral and max values
6. **Send keepalive** (`0601 2700 00B8 67` + `0681 2700 XXYY ZZ`) during idle periods to maintain connection

### Common Pitfalls

- Sending mode commands as single packets instead of 3-command groups
- Using the wrong write characteristic (handle 40 vs handle 43)
- Not sending the initialization sequence before attempting movement
- Computing bytes 4-6 instead of using capture-derived values
- Not sending keepalive during idle (connection may drop)

---

## 10. Raw Capture Data Summary

### Chronological Recording Order

| Recording | Time | Duration | Commands | Key Action |
|-----------|------|----------|----------|------------|
| panright.txt | 15:44:45 | ~4 sec | 42 writes | Pan right movement |
| panleft.txt | 15:45:07 | ~5 sec | 58 writes | Pan left (includes `0601 05` init) |
| tiltup.txt | 15:45:51 | ~3 sec | 45 writes | Tilt up movement |
| tiltdown.txt | 15:46:33 | ~4 sec | 57 writes | Tilt down movement |
| changemodes.txt | 15:47:08 | ~6 sec | 6 writes | Keepalive only |
| connect.txt | 15:49:46 | ~8 sec | 18 writes | Full connection + init |

### Known-Good Command Bytes (Steady State)

These are the exact bytes that work when sent to the gimbal:

```python
# Neutral (all axes centered)
CMD1_NEUTRAL = "061001080068BB"
CMD2_ALWAYS  = "061002080031EB"
CMD3_NEUTRAL = "061003080006DB"

# Tilt up (max speed)
CMD1_TILT_UP = "0610010FFFEFDC"
CMD3_TILT_UP = "061003080CC757"  # pan stays ~neutral

# Tilt down (max speed)
CMD1_TILT_DN = "0610010003D171"
CMD3_TILT_DN = "0610030793A53F"  # pan stays ~neutral

# Pan left (max speed)
CMD1_PAN_LT  = "061001079D2A91"  # tilt stays ~neutral
CMD3_PAN_LT  = "0610030003BF11"

# Pan right (max speed)
CMD1_PAN_RT  = "061001082D9D74"  # tilt stays ~neutral
CMD3_PAN_RT  = "0610030FFF81BC"

# Initialization commands
INIT_SPEED   = "060105000050C1"  # send 3x
INIT_PARAM   = "060102000055D1"
INIT_START   = "060106000991"    # final "go" command
```
