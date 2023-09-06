import asyncio
import json
import os
import shutil
import threading
from sys import exit

from misskey import Misskey
import re
import requests
import websockets
from termux.Notification import notify

# メモ
# icon : https://romannurik.github.io/AndroidAssetStudio/icons-notification.html#source.type=clipart&source.clipart=ac_unit&source.space.trim=1&source.space.pad=0&name=ic_stat_ac_unit
# image-path : path?

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

mk = Misskey(config['host'], i= config['i'])

me = mk.i()



async def runner():
    # try:
        async with websockets.connect(ws_url) as ws:  # type: ignore
            print('ws connect')
            await ws.send(
                json.dumps({"type": "connect", "body": {"channel": "main", "id": "1"}})
            )
            print('ready')
            while True:
                recv = json.loads(await ws.recv())
                print(recv) # デバッグ用
                recv_body = recv['body']['body']
                if recv['body']['type'] == 'notification':

                    """try:
                        imgData = requests.get(recv_body['user']['avatarUrl'], stream=True)
                        if imgData.status_code == 200:
                            try:
                                with open(f'.data/{recv_body["user"]["id"]}.png', 'xb') as f:
                                    imgData.raw.decode_content = True
                                    shutil.copyfileobj(imgData.raw, f)
                            except FileExistsError:
                                pass
                            notifier.icon = f'.data/{recv_body["user"]["id"]}.png'
                    except KeyError:
                        notifier.icon = f'icon/icon.png'"""

                    match recv_body['type']:
                        case 'reaction':
                            if re.match(r'.+@', recv_body['reaction']) != None:
                                emoji =  re.match(r'.+@', recv_body['reaction'])
                                title = f"{recv_body['user']['name']}が{emoji.group()[1:-1]}でリアクションしました"
                            else:
                                emoji = recv_body['reaction']
                                title = f"{recv_body['user']['name']}が{emoji}でリアクションしました"
                            message = recv_body['note']['text']
                            notify(title, message)

                        case 'reply':
                            msg = re.sub(r'(@.+@.+\..+\s)', '', recv_body['note']['text'], len(re.findall(r'(@.+@.+\..+\s)', recv_body['note']['text'])))
                            notifier.title = f"{recv_body['user']['name']}が返信しました"
                            notifier.message = f"{msg}\n------------\n{recv_body['note']['reply']['text']}"
                            notifier.send()

                        case 'mention':
                            msg = re.sub(r'(@.+@.+\..+\s)', '', recv_body['note']['text'], len(re.findall(r'(@.+@.+\..+\s)', recv_body['note']['text'])))
                            notifier.title = f"{recv_body['user']['name']}がメンションしました"
                            notifier.message = msg
                            notifier.send()

                        case 'renote':
                            notifier.title = f"{recv_body['user']['name']}がリノートしました"
                            notifier.message = recv_body['note']['renote']['text']
                            notifier.send()

                        case 'quote':
                            notifier.title = f"{recv_body['user']['name']}が引用リノートしました"
                            notifier.message = f'{recv_body["note"]["text"]}\n-------------\n{recv_body["note"]["renote"]["text"]}'
                            notifier.send()

                        case 'follow':
                            notifier.title = f"{recv_body['user']['name']}@{recv_body['user']['host']}"
                            notifier.message = 'ホョローされました'
                            notifier.send()

                        case 'followRequestAccepted':
                            notifier.title = f"{recv_body['user']['name']}@{recv_body['user']['host']}"
                            notifier.message = 'ホョローが承認されました'
                            notifier.send()

                        case 'receiveFollowRequest':
                            notifier.title = f"{recv_body['user']['name']}@{recv_body['user']['host']}"
                            notifier.message = 'ホョローがリクエストされました'
                            notifier.send()

                        case 'pollEnded': #TODO:自分のときは名前表示省略
                            imgData = requests.get(recv_body['user']['avatarUrl'], stream=True)
                            if imgData.status_code == 200:
                                try:
                                    with open(f'.data/{recv_body["user"]["id"]}.png', 'xb') as f:
                                        imgData.raw.decode_content = True
                                        shutil.copyfileobj(imgData.raw, f)
                                except FileExistsError:
                                    pass
                            votes = 0
                            most_vote = None
                            voted = None
                            if recv_body['note']['user']['id'] == me['id']:
                                notifier.title = f'自身が開始したアンケートの結果が出ました'
                            else:
                                notifier.title = f'{recv_body["note"]["user"]["name"]}のアンケートの結果が出ました'
                            notifier.message = f'{recv_body["note"]["text"]}\n------------'
                            for choice in recv_body['note']['poll']['choices']:
                                if choice['isVoted']:
                                    voted = choice
                                else:
                                    if choice['votes'] > votes:
                                        most_vote = choice
                                        votes = choice['votes']
                            if most_vote is None:
                                notifier.message += f"\n✅🏆:{voted['text']}|{voted['votes']}票"
                            else:
                                if voted is not None:
                                    notifier.message += f"\n✅  :{voted['text']}|{voted['votes']}票"
                                notifier.message += f"\n  🏆:{most_vote['text']}|{most_vote['votes']}票"
                            notifier.send()

                        case 'app':
                            notifier.title = recv_body['header']
                            notifier.message = recv_body['body']
                            imgData = requests.get(recv_body['icon'], stream=True)
                            if imgData.status_code == 200:
                                try:
                                    with open(f'.data/{recv_body["header"]}.png', 'xb') as f:
                                        imgData.raw.decode_content = True
                                        shutil.copyfileobj(imgData.raw, f)
                                except FileExistsError:
                                    pass
                                notifier.icon = f'.data/{recv_body["header"]}.png'
                            notifier.send()
                else:
                    pass

"""    except Exception  as err:
        print(f'Error:\n{err}')
        icon.stop()"""



print('client_startup...')

asyncio.run(runner())
