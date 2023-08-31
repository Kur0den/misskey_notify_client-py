import websockets
import json
import asyncio
from dotenv import load_dotenv
from os import environ as env

load_dotenv()

domain = "koliosky.com"
i = env["MISSKEY_API_TOKEN"]
ws_url = f"wss://{domain}/streaming?i={i}"


async def runner():
    async with websockets.connect(ws_url) as ws:  # type: ignore
        await ws.send(
            json.dumps({"type": "connect", "body": {"channel": "main", "id": "1"}})
        )
        while True:
            recv = json.loads(await ws.recv())
            if recv['body']['type'] != 'readAllNotifications':
                print(recv)


print('ready')
asyncio.get_event_loop().run_until_complete(runner())