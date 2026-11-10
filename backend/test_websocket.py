
import asyncio
import getpass
import json
import websockets


async def test_websocket():
    token = getpass.getpass("Enter your access token: ")

    url = f"ws://127.0.0.1:8000/ws?token={token}"

    try:
        async with websockets.connect(url) as ws:
            print("WebSocket connected successfully!")

            response = await asyncio.wait_for(ws.recv(), timeout=5)
            print("Server response:", json.loads(response))

            await ws.send(json.dumps({"action": "ping"}))

            pong = await asyncio.wait_for(ws.recv(), timeout=5)
            print("Ping response:", json.loads(pong))

            print("WebSocket test completed. Connection remains open.")

            await asyncio.sleep(60)

    except Exception as error:
        print("WebSocket error:", error)


if __name__ == "__main__":
    asyncio.run(test_websocket())
