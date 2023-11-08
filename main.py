import asyncio
import json
import os
import re
import shutil
from glob import glob
from hashlib import sha256
from io import BytesIO
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


if os.path.exists("config.json"):  # config.jsonが存在するかどうかの確認
    config = json.load(
        open(file="config.json", mode="r", encoding="UTF-8")
    )  # 存在する場合openして中身を変数に格納
    domain = config["host"]
    i = config["i"]
else:
    config = {}  # 存在しない場合インスタンスドメイン+トークンを聞きconfig.jsonを新規作成&保存
    config["host"] = input("ドメインを入力してください(例:example.com)-> https:// ")
    config["i"] = input('"通知を見る"の権限を有効にしたAPIトークンを入力してください->')
    print("初期設定が完了しました\n誤入力した/再設定をしたい場合は`config.json`を削除してください")
    json.dump(config, fp=open(file="config.json", mode="x", encoding="UTF-8"))
ws_url = f'wss://{config["host"]}/streaming?i={config["i"]}'

if not os.path.exists(".data"):  # 画像保存用の.dataフォルダが存在しない場合作成するように
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
    async def save_image(url: str | dict, name: str | None = None) -> str:
        """
        通知に使用する画像が存在するかどうか確認するための関数
        画像が存在した場合その画像のパスを返し
        画像が存在しない場合その画像をダウンロードしてパスを返す

        Args:
            url (str | dict): 確認する画像のURL
                                ユーザーのアイコンの場合はrecv_body['user'](dict)をそのまま突っ込む
                                アプリのアイコンの場合は画像のURL(str)をそのまま突っ込む
            name (str | None, optional): アプリの画像を確認する場合にアプリ名を突っ込む
                                                ユーザーのアイコン確認の際は無視して可

        Returns:
            image_path str: 画像のパス
        """

        if isinstance(url, dict):  # 引数urlがdictかどうか(指定されているのがユーザーのアイコンなのか)を判断
            name: str = url["id"]  # 画像保存時の名前用にuidを格納
            url: str = url["avatarUrl"]  # 引数から画像URLを取得し再格納
        img_path = glob(f"./.data/{name}.*")
        if img_path is not []:
            img_path = img_path[0]
            try:
                with open(img_path, mode="rb") as f:
                    img_binary = f.read()
                img_data = requests.get(url, timeout=10)
            except requests.exceptions.ConnectionError:
                return "icon/icon.png"
            if sha256(img_binary).hexdigest() == sha256(img_data.content).hexdigest():
                return img_path  # ファイルが既に存在し、サーバー上のデータと同じ場合はその画像のパスを返す
        try:
            img_data = requests.get(url, timeout=10)  # 画像が存在しなかった場合画像データをダウンロード
            if img_data.status_code == 200:  # ステータスが200かどうかを確認
                with BytesIO(img_data.content) as buf:
                    img = Image.open(buf)
                    img_path = f"{name}.{img.format.lower()}"  # 返り値用の変数にパスを格納
                    img.save(img_path)  # なんやかんや保存
            else:
                # TODO: 画像取得が失敗した旨のログを出力する
                img_path = "icon/icon.png"  # 返り値用の変数にアプリアイコンのパスを格納
        except requests.exceptions.ConnectionError:
            # TODO: 画像取得時に接続失敗した旨のログを出力する
            img_path = "icon/icon.png"  # 返り値用の変数にアプリアイコンのパスを格納

        return img_path  # みんな大好きreturn

    @staticmethod
    async def notify_def(title: str, content: str, img: str) -> None:
        """
        通知を送信するための関数

        Args:
            title (str): 通知のタイトル
            content (str): 通知の内容
            img (str): 通知に表示する画像のパス
        """

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
