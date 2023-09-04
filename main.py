import asyncio
import json
import os
import shutil

import re
import requests
import websockets
from notifypy import Notify

notifier = Notify()

# ignore_events = ['unreadNotification', 'readAllNotifications', 'unreadMention', 'readAllUnreadMentions', 'unreadSpecifiedNote', 'readAllUnreadSpecifiedNotes', 'unreadMessagingMessage', 'readAllMessagingMessages']

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
    try:
        async with websockets.connect(ws_url) as ws:  # type: ignore
            print('ws connected')
            await ws.send(
                json.dumps({"type": "connect", "body": {"channel": "main", "id": "1"}})
            )
            while True:
                recv = json.loads(await ws.recv())
                print(recv) # デバッグ用
                recv_body = recv['body']['body']
                if recv['body']['type'] == 'notification':
                    imgData = requests.get(recv_body['user']['avatarUrl'], stream=True)
                    if imgData.status_code == 200:
                        try:
                            with open(f'.data/{recv_body["user"]["id"]}.png', 'xb') as f:
                                imgData.raw.decode_content = True
                                shutil.copyfileobj(imgData.raw, f)
                        except FileExistsError:
                            pass
                    match recv_body['type']:
                        case 'reaction':
                            if re.match(r'.+@', recv_body['reaction']) != None:
                                emoji =  re.match(r'.+@', recv_body['reaction'])
                                notifier.title = f"{recv_body['user']['name']}が{emoji.group()[1:-1]}でリアクションしました"
                            else:
                                emoji = recv_body['reaction']
                                notifier.title = f"{recv_body['user']['name']}が{emoji}でリアクションしました"
                            notifier.message = recv_body['note']['text']
                            notifier.icon = f'.data/{recv_body["user"]["id"]}.png'
                            notifier.send()

                        case 'reply':
                            msg = re.sub(r'(@.+@.+\..+\s)', '', recv_body['note']['text'], len(re.findall(r'(@.+@.+\..+\s)', recv_body['note']['text'])))
                            notifier.title = f"{recv_body['user']['name']}が返信しました"
                            notifier.message = msg
                            notifier.icon = f'.data/{recv_body["user"]["id"]}.png'
                            notifier.send()

                        case 'mention':
                            msg = re.sub(r'(@.+@.+\..+\s)', '', recv_body['note']['text'], len(re.findall(r'(@.+@.+\..+\s)', recv_body['note']['text'])))
                            notifier.title = f"{recv_body['user']['name']}がメンションしました"
                            notifier.message = msg
                            notifier.icon = f'.data/{recv_body["user"]["id"]}.png'
                            notifier.send()
                else:
                    pass

    except Exception  as err:
        print(f'Error:\n{err}')


print('client_ready')
asyncio.get_event_loop().run_until_complete(runner())

