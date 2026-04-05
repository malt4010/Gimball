import asyncio
from bleak import BleakClient

ADDRESS = "F0DB9D5E-3C0B-EA83-A326-CF59FB0D9019"
# Candidate characteristic UUIDs
CHAR_UUIDS = [
    "0000c303-0000-1000-8000-00805f9b34fb", # write-without-response
    "0000c302-0000-1000-8000-00805f9b34fb", # write
    "0000c304-0000-1000-8000-00805f9b34fb", # write
]

# UP command packet from dump
UP_PACKET = "55130403270248094004570000000001"

async def test_move(client, char_uuid):
    print(f"Testing movement on {char_uuid}...")
    try:
        data = bytearray.fromhex(UP_PACKET)
        for i in range(50): # ~1 second at 50Hz
            await client.write_gatt_char(char_uuid, data, response=False)
            await asyncio.sleep(0.02)
        print(f"Finished test on {char_uuid}")
    except Exception as e:
        print(f"Error on {char_uuid}: {e}")

async def main():
    print(f"Connecting to {ADDRESS}...")
    async with BleakClient(ADDRESS) as client:
        if client.is_connected:
            print("Connected!")
            for uuid in CHAR_UUIDS:
                await test_move(client, uuid)
                await asyncio.sleep(1) # Gap between tests
        else:
            print("Failed to connect.")

if __name__ == "__main__":
    asyncio.run(main())
