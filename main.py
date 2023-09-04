import asyncio
import json
import os


import websockets
from notifypy import Notify

notifier = Notify()

if os.path.exists('config.json'):
    config = json.load(open('config.json', 'r'))
    domain = config['host']
    i = config['i']
    ws_url = f"wss://{config['host']}/streaming?i={config['i']}"
else:
    config = {}
    config['host'] = input('ドメインを入力してください(例:example.com)->')
    config['i'] = input('"通知を見る"の権限を有効にしたAPIトークンを入力してください->')
    print('初期設定が完了しました\n誤入力した/再設定をしたい場合は`config.json`を削除してください')
    json.dump(config, open("config.json",'x'))
    ws_url = f"wss://{config['host']}/streaming?i={config['i']}"



async def runner():
#    try:
        async with websockets.connect(ws_url) as ws:  # type: ignore
            print('ws connected')
            await ws.send(
                json.dumps({"type": "connect", "body": {"channel": "main", "id": "1"}})
            )
            while True:
                recv = json.loads(await ws.recv())
                recv_body = recv['body']['body']
                if recv['body']['type'] != 'readAllNotifications':
                    match recv_body['type']:
                        case 'reaction':
                            notifier.title = f"{recv_body['user']['name']}が{recv_body['reaction']}でリアクションしました"
                            notifier.message = recv_body['note']['text']
                            notifier.icon = recv_body['user']['avatarUrl']
                            notifier.send()
                            print('reaction')
                        case 'readAllNotifications':
                            pass
                else:
                    pass
                print(recv)

"""    except Exception  as err:
        print(f'Error:\n{err}')"""


print('client_ready')
asyncio.get_event_loop().run_until_complete(runner())

