import asyncio
import json
import logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from bleak import BleakClient, BleakError
import os

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# GIMBAL CONFIG
# From inspection: 0000c303 is write-without-response (Handle 50)
GIMBAL_ADDRESS = "F0DB9D5E-3C0B-EA83-A326-CF59FB0D9019"
WRITE_UUID = "0000c303-0000-1000-8000-00805f9b34fb"

# DIRECTION MAPPING (LE encoded bytes for positions 6 & 7)
# Note: XX XX magnitude/direction. The user provided hex strings.
COMMANDS = {
    "up": "55130403270248094004570000000001",
    "down": "551304032702200e4004570000000001",
    "left": "5513040327028d054004570000000001",
    "right": "551304032702840c4004570000000001",
    "stop": "55130403270200004004570000000001", # Assuming 0000 is neutral
}

class GimbalController:
    def __init__(self, address, char_uuid):
        self.address = address
        self.char_uuid = char_uuid
        self.client = None
        self.current_command = "stop"
        self.is_running = False
        self.loop_task = None

    async def connect(self):
        try:
            from bleak import BleakScanner
            logger.info(f"Connecting to Gimbal at {self.address}...")
            
            # Try direct connection to address first
            self.client = BleakClient(self.address)
            connected = await self.client.connect()
            
            if not connected:
                logger.warning("Direct connection failed. Scanning for OMSE device...")
                device = await BleakScanner.find_device_by_name("OMSE", timeout=10.0)
                if device:
                    logger.info(f"Found OMSE device at {device.address}. Retrying...")
                    self.address = device.address
                    self.client = BleakClient(device)
                    connected = await self.client.connect()

            if connected:
                logger.info("Connected to Gimbal!")
                self.is_running = True
                self.loop_task = asyncio.create_task(self.control_loop())
                return True
            else:
                logger.error("Failed to connect.")
                return False
        except Exception as e:
            logger.error(f"Connect error: {e}")
            return False

    async def disconnect(self):
        self.is_running = False
        if self.loop_task:
            self.loop_task.cancel()
        if self.client and self.client.is_connected:
            await self.client.disconnect()
            logger.info("Disconnected from Gimbal.")

    async def control_loop(self):
        logger.info("Starting control loop (50Hz)...")
        while self.is_running:
            try:
                if self.client and self.client.is_connected:
                    cmd_hex = COMMANDS.get(self.current_command, COMMANDS["stop"])
                    data = bytearray.fromhex(cmd_hex)
                    await self.client.write_gatt_char(self.char_uuid, data, response=False)
                else:
                    # Not connected, try to reconnect every 5 seconds
                    logger.warning("Gimbal disconnected. Retrying in 5s...")
                    await asyncio.sleep(5)
                    await self.connect()
            except Exception as e:
                logger.error(f"Loop error: {e}")
                await asyncio.sleep(1) # Gap on error
            await asyncio.sleep(0.02) # 50Hz

    def set_command(self, direction):
        if direction in COMMANDS:
            self.current_command = direction
        else:
            self.current_command = "stop"

controller = GimbalController(GIMBAL_ADDRESS, WRITE_UUID)

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Attempt to connect to gimbal
    connect_task = asyncio.create_task(controller.connect())
    yield
    # Shutdown: Disconnect
    await controller.disconnect()

app = FastAPI(lifespan=lifespan)

@app.get("/")
async def get_index():
    return FileResponse("index.html")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    logger.info("WebSocket connected from UI")
    try:
        while True:
            data = await websocket.receive_text()
            logger.info(f"WS RAW RECEIVED: {data}")
            msg = json.loads(data)
            action = msg.get("action")
            direction = msg.get("direction")

            if action == "move":
                # NippleJS sometimes sends lowercase but our COMMANDS dict uses it too.
                # Important: check direction string specifically.
                if hasattr(direction, 'get'): # direction might be object
                    dir_str = direction.get('angle', 'stop')
                else:
                    dir_str = str(direction)
                
                logger.info(f"MOVE REQUEST: {dir_str}")
                controller.set_command(dir_str.lower())
            elif action == "stop":
                controller.set_command("stop")
                logger.info("STOP REQUEST")
            
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
        controller.set_command("stop")
    except Exception as e:
        logger.error(f"WS Error: {e}")
        controller.set_command("stop")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, loop="asyncio")
