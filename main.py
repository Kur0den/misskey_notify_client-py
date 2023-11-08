import asyncio
import json
import os
import re
import shutil
from sys import exit

import pystray
import requests
import websockets
from misskey import Misskey
from misskey import exceptions as mk_exceptions
from notifypy import Notify
from PIL import Image

notifier = Notify()

app_name = "Misskey-Notify-Client"
app_icon = "icon/icon.png"

# ignore_events = ['unreadNotification', 'readAllNotifications', 'unreadMention', 'readAllUnreadMentions', 'unreadSpecifiedNote', 'readAllUnreadSpecifiedNotes', 'unreadMessagingMessage', 'readAllMessagingMessages']


if os.path.exists("config.json"):
    config = json.load(open(file="config.json", mode="r", encoding="UTF-8"))
    domain = config["host"]
    i = config["i"]
else:
    config = {}
    config["host"] = input("ドメインを入力してください(例:example.com)-> https:// ")
    config["i"] = input('"通知を見る"の権限を有効にしたAPIトークンを入力してください->')
    print("初期設定が完了しました\n誤入力した/再設定をしたい場合は`config.json`を削除してください")
    json.dump(config, fp=open(file="config.json", mode="x", encoding="UTF-8"))
ws_url = f'wss://{config["host"]}/streaming?i={config["i"]}'

if not os.path.exists(".data"):  # フォルダが存在しない場合作成するように
    os.mkdir(".data")
# 生存確認
try:
    resp_code = requests.request("GET", f'https://{config["host"]}').status_code
except requests.exceptions.ConnectionError:
    print("サーバーへの接続ができませんでした\n入力したドメインが正しいかどうかを確認してください")
    exit()
match resp_code:
    case 404:
        print(
            "API接続ができませんでした\n - 利用しているインスタンスが正常に稼働しているか\n - 入力したドメインが正しいかどうか\nを確認してください"
        )
        exit()
    case 410 | 500 | 502 | 503:
        print(
            "サーバーが正常に応答しませんでした\n利用しているインスタンスが正常に稼働しているかを確認してください\nStatusCode:",
            resp_code,
        )
        exit()
    case 429:
        print("レートリミットに達しました\nしばらくしてから再実行してください")
        exit()

try:
    mk = Misskey(config["host"], i=config["i"])
except requests.exceptions.ConnectionError:
    print("ドメインが違います\nconfig.jsonを削除/編集してもう一度入力しなおしてください")
    exit()
except mk_exceptions.MisskeyAuthorizeFailedException:
    print("APIキーが違います\nconfig.jsonを削除/編集して入力しなおしてください")
    exit()
me = mk.i()


class main:
    def __init__(self) -> None:
        # self.loop = asyncio.get_event_loop()
        self.websocket_task = None
        self.icon_task = None

    @staticmethod
    async def notify_def(title: str, content: str, img: str | dict) -> None:
        """
        ### 通知を送信するための関数
        title: str
            通知のタイトルに表示する文字
        content: str
            通知の内容
        img: str | dict
            通知に表示する画像のパス
            dictはwebsocketのrecvそのまま突っ込む用
        """
        if isinstance(img, dict):
            try:
                img_Data = requests.get(img["avatarUrl"], stream=True, timeout=10)
                if img_Data.status_code == 200:
                    try:
                        with open(f'.data/{img["id"]}.png', "xb") as file:
                            img_Data.raw.decode_content = True
                            shutil.copyfileobj(img_Data.raw, file)
                    except FileExistsError:
                        pass
                    img = f'.data/{img["id"]}.png'
            except KeyError:
                img = "icon/icon.png"
        notifier.title = title
        notifier.message = content
        notifier.icon = img
        notifier.send()

    @staticmethod
    async def websocket_connect():
        """websocket接続するためのやつ"""
        while True:
            try:
                async with websockets.connect(ws_url) as ws:
                    print("ws connect")
                    await ws.send(
                        json.dumps(
                            {"type": "connect", "body": {"channel": "main", "id": "1"}}
                        )
                    )
                    print("ready")
                    while True:
                        recv = json.loads(await ws.recv())
                        print(recv)  # デバッグ用
                        if recv["type"] == "channel":
                            if recv["body"]["type"] == "notification":
                                recv_body = recv["body"]["body"]
                                match recv_body["type"]:
                                    case "reaction":
                                        if (
                                            re.match(r".+@", recv_body["reaction"])
                                            is not None
                                        ):
                                            emoji = re.match(
                                                r".+@", recv_body["reaction"]
                                            )
                                            title = f'{recv_body["user"]["name"]}が{emoji.group()[1:-1]}でリアクションしました'
                                        else:
                                            emoji = recv_body["reaction"]
                                            title = f'{recv_body["user"]["name"]}が{emoji}でリアクションしました'
                                        await main.notify_def(
                                            title=title,
                                            content=recv_body["note"]["text"],
                                            img=recv_body["user"],
                                        )

                                    case "reply":
                                        msg = re.sub(
                                            pattern=r"(@.+@.+\..+\s)",
                                            repl="",
                                            string=recv_body["note"]["text"],
                                            count=len(
                                                re.findall(
                                                    pattern=r"(@.+@.+\..+\s)",
                                                    string=recv_body["note"]["text"],
                                                )
                                            ),
                                        )
                                        await main.notify_def(
                                            title=f'{recv_body["user"]["name"]}が返信しました',
                                            content=f'{msg}\n------------\n{recv_body["note"]["reply"]["text"]}',
                                            img=recv_body["user"],
                                        )

                                    case "mention":
                                        await main.notify_def(
                                            title=f'{recv_body["user"]["name"]}がメンションしました',
                                            content=re.sub(
                                                pattern=r"(@.+@.+\..+\s)",
                                                repl="",
                                                string=recv_body["note"]["text"],
                                                count=len(
                                                    re.findall(
                                                        pattern=r"(@.+@.+\..+\s)",
                                                        string=recv_body["note"][
                                                            "text"
                                                        ],
                                                    )
                                                ),
                                            ),
                                            img=recv_body["user"],
                                        )

                                    case "renote":
                                        await main.notify_def(
                                            title=f'{recv_body["user"]["name"]}がリノートしました',
                                            content=recv_body["note"]["renote"]["text"],
                                            img=recv_body["user"],
                                        )

                                    case "quote":
                                        await main.notify_def(
                                            title=f'{recv_body["user"]["name"]}が引用リノートしました',
                                            content=f'{recv_body["note"]["text"]}\n-------------\n{recv_body["note"]["renote"]["text"]}',
                                            img=recv_body["user"],
                                        )

                                    case "follow":
                                        await main.notify_def(
                                            title=f'{recv_body["user"]["name"]}@{recv_body["user"]["host"]}',
                                            content="ホョローされました",
                                            img=recv_body["user"],
                                        )

                                    case "followRequestAccepted":
                                        await main.notify_def(
                                            title=f'{recv_body["user"]["name"]}@{recv_body["user"]["host"]}',
                                            content="ホョローが承認されました",
                                            img=recv_body["user"],
                                        )

                                    case "receiveFollowRequest":
                                        await main.notify_def(
                                            title=f'{recv_body["user"]["name"]}@{recv_body["user"]["host"]}',
                                            content="ホョローがリクエストされました",
                                            img=recv_body["user"],
                                        )

                                    case "pollEnded":
                                        img_data = requests.get(
                                            recv_body["user"]["avatarUrl"],
                                            stream=True,
                                            timeout=config["timeout"],
                                        )
                                        if img_data.status_code == 200:
                                            try:
                                                with open(
                                                    f'.data/{recv_body["user"]["id"]}.png',
                                                    "xb",
                                                ) as f:
                                                    img_data.raw.decode_content = True
                                                    shutil.copyfileobj(img_data.raw, f)
                                            except FileExistsError:
                                                pass
                                        votes = 0
                                        most_vote = None
                                        voted = None
                                        if recv_body["note"]["user"]["id"] == me["id"]:
                                            title = "自身が開始したアンケートの結果が出ました"
                                        else:
                                            title = f'{recv_body["note"]["user"]["name"]}のアンケートの結果が出ました'
                                        message = (
                                            f'{recv_body["note"]["text"]}\n------------'
                                        )
                                        for choice in recv_body["note"]["poll"][
                                            "choices"
                                        ]:
                                            if choice["isVoted"]:
                                                voted = choice
                                            else:
                                                if choice["votes"] > votes:
                                                    most_vote = choice
                                                    votes = choice["votes"]
                                        if most_vote is None:
                                            message += f'\n✅🏆:{voted["text"]}|{voted["votes"]}票'
                                        else:
                                            if voted is not None:
                                                message += f'\n✅  :{voted["text"]}|{voted["votes"]}票'
                                            message += f'\n  🏆:{most_vote["text"]}|{most_vote["votes"]}票'
                                        await main.notify_def(
                                            title=title,
                                            content=message,
                                            img=f'.data/{recv_body["header"]}.png',
                                        )

                                    case "app":
                                        img_data = requests.get(
                                            recv_body["icon"],
                                            stream=True,
                                            timeout=config["timeout"],
                                        )
                                        if img_data.status_code == 200:
                                            try:
                                                with open(
                                                    f'.data/{recv_body["header"]}.png',
                                                    "xb",
                                                ) as file:
                                                    img_data.raw.decode_content = True
                                                    shutil.copyfileobj(
                                                        img_data.raw, file
                                                    )
                                            except FileExistsError:
                                                pass
                                        await main.notify_def(
                                            title=recv_body["header"],
                                            content=recv_body["body"],
                                            img=f'.data/{recv_body["header"]}.png',
                                        )
                            else:
                                pass
            except websockets.exceptions.ConnectionClosedError:
                print("websocket disconnected. reconecting...")
                await main.notify_def(
                    title=app_name, content="サーバーから切断されました\n5秒後に再接続します...", img=app_icon
                )
                await asyncio.sleep(5)

    def stopper(self):
        """アプリ終了時に呼び出す関数"""
        main.websocket_task.cancel()
        icon.stop()

    async def runner(self, icon):
        """
        ### アプリ起動時に呼び出されるやつ
        引数:
            icon:
        """
        self.websocket_task = asyncio.create_task(main.websocket_connect())
        self.icon_task = asyncio.create_task(asyncio.to_thread(icon.run))

        try:
            await self.websocket_task
            await self.icon_task
        except asyncio.CancelledError:
            print("task cancelled")


main = main()


def notify_read():
    """
    ### 通知を全部既読にする際に呼び出す関数
    引数: 無し
    """
    return_read = mk.notifications_mark_all_as_read()
    if return_read:
        message = "通知をすべて既読にしました"
    else:
        message = "通知の既読化に失敗しました"
    asyncio.run(main.notify_def(title=app_name, content=message, img=app_icon))


icon = pystray.Icon(
    "Misskey-notify-client",
    icon=Image.open(app_icon),
    menu=pystray.Menu(
        pystray.MenuItem("すべて既読にする", notify_read, checked=None),
        pystray.MenuItem("終了", main.stopper, checked=None),
    ),
)
# TODO: どの通知受け取るか設定できるように

print("client_startup...")
# icon_thread = threading.Thread(target=icon.run).start()
print("icon starting...")

asyncio.run(main.runner(icon))
