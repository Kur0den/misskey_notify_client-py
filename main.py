import asyncio
import json
import os
import re
import shutil
import threading
from sys import exit

import pystray
import requests
import websockets
from misskey import Misskey
from misskey import exceptions as mk_exceptions
from notifypy import Notify
from PIL import Image

notifier = Notify()


# ignore_events = ['unreadNotification', 'readAllNotifications', 'unreadMention', 'readAllUnreadMentions', 'unreadSpecifiedNote', 'readAllUnreadSpecifiedNotes', 'unreadMessagingMessage', 'readAllMessagingMessages']

if os.path.exists('config.json'):
    config = json.load(open('config.json', 'r'))
    domain = config['host']
    i = config['i']
else:
    config = {}
    config['host'] = input('ドメインを入力してください(例:example.com)->')
    config['i'] = input('"通知を見る"の権限を有効にしたAPIトークンを入力してください->')
    print('初期設定が完了しました\n誤入力した/再設定をしたい場合は`config.json`を削除してください')
    json.dump(config, fp=open("config.json",'x'))
ws_url = f"wss://{config['host']}/streaming?i={config['i']}"

# 生存確認
resp_code = requests.request('GET',f'https://{config["host"]}').status_code
match resp_code:
    case 404:
        print('API接続ができませんでした\n - 利用しているインスタンスが正常に稼働しているか\n - 入力したドメインが正しいかどうか\nを確認してください')
        exit()
    case 410 | 500 | 502 | 503:
        print('サーバーが正常に応答しませんでした\n利用しているインスタンスが正常に稼働しているかを確認してください\nStatusCode:', resp_code)
        exit()
    case 429:
        print('レートリミットに達しました\nしばらくしてから再実行してください')
        exit()

try:
    mk = Misskey(config['host'], i= config['i'])
except requests.exceptions.ConnectionError:
    print('ドメインが違います\nconfig.jsonを削除/編集してもう一度入力しなおしてください')
    #os.remove('config.json')
    exit()
except mk_exceptions.MisskeyAuthorizeFailedException:
    print('APIキーが違います\nconfig.jsonを削除/編集して入力しなおしてください')
    #os.remove('config.json')
    exit()
me = mk.i()

async def notify_def(title: str, message: str, icon:str | dict):
    if type(icon) is dict:
        try:
            imgData = requests.get(icon['avatarUrl'], stream=True, timeout=10)
            if imgData.status_code == 200:
                try:
                    with open(f'.data/{icon["id"]}.png', 'xb') as f:
                        imgData.raw.decode_content = True
                        shutil.copyfileobj(imgData.raw, f)
                except FileExistsError:
                    pass
                icon = f'.data/{icon["id"]}.png'
        except KeyError:
            icon = 'icon/icon.png'
    notifier.title = title
    notifier.message = message
    notifier.icon = icon
    notifier.send()


async def runner():
    async with websockets.connect(ws_url) as ws:
        print('ws connect')
        await ws.send(
            json.dumps({"type": "connect", "body": {"channel": "main", "id": "1"}})
        )
        print('ready')
        while True:
            recv = json.loads(await ws.recv())
            print(recv) # デバッグ用
            if recv['body']['type'] == 'notification':
                recv_body = recv['body']['body']
                match recv_body['type']:
                    case 'reaction':
                        if re.match(r'.+@', recv_body['reaction']) != None:
                            emoji = re.match(r'.+@', recv_body['reaction'])
                            title = f"{recv_body['user']['name']}が{emoji.group()[1:-1]}でリアクションしました"
                        else:
                            emoji = recv_body['reaction']
                            title = f"{recv_body['user']['name']}が{emoji}でリアクションしました"
                        await notify_def(title,
                                         recv_body['note']['text'],
                                         recv_body['user'])


                    case 'reply':
                        msg = re.sub(
                                        pattern=r'(@.+@.+\..+\s)',
                                        repl='',
                                        string=recv_body['note']['text'],
                                        count=len(
                                            re.findall(
                                                pattern=r'(@.+@.+\..+\s)',
                                                string=recv_body['note']['text'])))
                        await notify_def(f"{recv_body['user']['name']}が返信しました",
                                         f"{msg}\n------------\n{recv_body['note']['reply']['text']}",
                                         recv_body['user'])

                    case 'mention':
                        await notify_def(f'{recv_body["user"]["name"]}がメンションしました',
                                         re.sub(
                                                r'(@.+@.+\..+\s)',
                                                '',
                                                recv_body['note']['text'],
                                                len(re.findall(r'(@.+@.+\..+\s)',
                                                               recv_body['note']['text'])))
                                         , recv_body['user'])

                    case 'renote':
                        await notify_def(f"{recv_body['user']['name']}がリノートしました",
                                         recv_body['note']['renote']['text'],
                                         recv_body['user'])

                    case 'quote':
                        await notify_def(f"{recv_body['user']['name']}が引用リノートしました",
                                         f'{recv_body["note"]["text"]}\n-------------\n{recv_body["note"]["renote"]["text"]}',
                                         recv_body['user'])

                    case 'follow':
                        await notify_def(f"{recv_body['user']['name']}@{recv_body['user']['host']}",
                                         'ホョローされました',
                                         recv_body['user'])

                    case 'followRequestAccepted':
                        await notify_def(title=f"{recv_body['user']['name']}@{recv_body['user']['host']}",
                                         message='ホョローが承認されました',
                                         icon=recv_body['user'])

                    case 'receiveFollowRequest':
                        await notify_def(f"{recv_body['user']['name']}@{recv_body['user']['host']}",
                                         'ホョローがリクエストされました',
                                         recv_body['user'])

                    case 'pollEnded':
                        img_data = requests.get(recv_body['user']['avatarUrl'], stream=True)
                        if img_data.status_code == 200:
                            try:
                                with open(f'.data/{recv_body["user"]["id"]}.png', 'xb') as f:
                                    img_data.raw.decode_content = True
                                    shutil.copyfileobj(img_data.raw, f)
                            except FileExistsError:
                                pass
                        votes = 0
                        most_vote = None
                        voted = None
                        if recv_body['note']['user']['id'] == me['id']:
                            title = f'自身が開始したアンケートの結果が出ました'
                        else:
                            title = f'{recv_body["note"]["user"]["name"]}のアンケートの結果が出ました'
                        message = f'{recv_body["note"]["text"]}\n------------'
                        for choice in recv_body['note']['poll']['choices']:
                            if choice['isVoted']:
                                voted = choice
                            else:
                                if choice['votes'] > votes:
                                    most_vote = choice
                                    votes = choice['votes']
                        if most_vote is None:
                            message += f"\n✅🏆:{voted['text']}|{voted['votes']}票"
                        else:
                            if voted is not None:
                                message += f"\n✅  :{voted['text']}|{voted['votes']}票"
                            message += f"\n  🏆:{most_vote['text']}|{most_vote['votes']}票"
                        await notify_def(title, message, f'.data/{recv_body["header"]}.png')

                    case 'app':
                        img_data = requests.get(recv_body['icon'], stream=True)
                        if img_data.status_code == 200:
                            try:
                                with open(f'.data/{recv_body["header"]}.png', 'xb') as f:
                                    img_data.raw.decode_content = True
                                    shutil.copyfileobj(img_data.raw, f)
                            except FileExistsError:
                                pass
                        await notify_def(recv_body['header'],
                                         recv_body['body'],
                                         f'.data/{recv_body["header"]}.png')
            else:
                pass


def notify_read():
    return_read = mk.notifications_mark_all_as_read()
    title = f'Misskey-Notify-Client'
    if return_read:
        message = '通知をすべて既読にしました'
    else:
        message = '通知の既読化に失敗しました'
    asyncio.run(notify_def(title, message, 'icon/icon.png'))

def stop():
    print('未実装だよ')
"""    try:
        exit()
    except SystemExit:
        pass
    icon.stop()
    try:
        task = asyncio.ensure_future(runner())
        task.cancel()
    except RuntimeWarning:
        pass"""


icon = pystray.Icon('Misskey-notify-client',icon=Image.open('icon/icon.png'), menu=pystray.Menu(
    pystray.MenuItem(
        'すべて既読にする',
        notify_read,
        checked=None),
    pystray.MenuItem(
        '終了(未実装)',
        stop,
        checked=None)))
# TODO: どの通知受け取るか設定できるように

print('client_startup...')
icon_thread = threading.Thread(target=icon.run).start()
print('icon starting...')

asyncio.run(runner())
