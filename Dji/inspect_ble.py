import asyncio
from bleak import BleakClient, BleakScanner

# ADDRESS = "F0DB9D5E-3C0B-EA83-A326-CF59FB0D9019"
# Actually I'll scan first to find it just in case address changed or needs to be discovered by name.
DEVICE_NAME = "OMSE-A03H91"

async def main():
    print(f"Scanning for {DEVICE_NAME}...")
    device = await BleakScanner.find_device_by_name(DEVICE_NAME)
    
    if not device:
        print("Device not found by name. Scanning all...")
        devices = await BleakScanner.discover()
        for d in devices:
            print(f"Found: {d.name} or {d.address}")
            if d.name and DEVICE_NAME in d.name:
                device = d
                break
    
    if not device:
        print(f"Could not find device {DEVICE_NAME}")
        return

    print(f"Connecting to {device.address}...")
    async with BleakClient(device.address) as client:
        print(f"Connected: {client.is_connected}")
        print("\n--- Services and Characteristics ---")
        for service in client.services:
            print(f"\nService: {service.uuid} ({service.description})")
            for char in service.characteristics:
                print(f"  Characteristic: {char.uuid} ({char.description})")
                print(f"    Handle: {char.handle}")
                print(f"    Properties: {', '.join(char.properties)}")
                
                # Check descriptors (to see if we can find the handle more easily)
                for descriptor in char.descriptors:
                    print(f"    Descriptor: {descriptor.uuid} - Handle: {descriptor.handle}")

if __name__ == "__main__":
    asyncio.run(main())
