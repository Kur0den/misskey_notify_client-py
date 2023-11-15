import asyncio
import json
import os
import re
from glob import glob
from hashlib import sha256
from io import BytesIO
from sys import exit
import logging

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

# logの設定
logging.basicConfig(
    format="%(asctime)s %(name)s - %(levelname)s: %(message)s",  # 出力のフォーマット
    datefmt="[%Y-%m-%dT%H:%M:%S%z]",  # 時間(asctime)のフォーマット
    filename="./latest.log",
    filemode="w",
    encoding="UTF-8",
)
log_main = logging.getLogger("main")
log_img = logging.getLogger("img_get")
log_notify = logging.getLogger("notifier")

ws_reconnect_count = 0

# ./config.jsonが存在するかどうかの確認
if os.path.exists("config.json"):
    config = json.load(
        open(file="config.json", mode="w", encoding="UTF-8")
    )  # 存在する場合openして中身を変数に格納
    match config["log_level"]:
        case "DEBUG":
            logging.basicConfig(level=logging.DEBUG)
        case "INFO":
            logging.basicConfig(level=logging.INFO)
        case "WARNING":
            logging.basicConfig(level=logging.WARNING)
        case "ERROR":
            logging.basicConfig(level=logging.ERROR)
        case "CRITICAL":
            logging.basicConfig(level=logging.CRITICAL)
    log_main.info("Config loaded")
else:
    config = {}  # 存在しない場合インスタンスドメイン+トークンを聞きconfig.jsonを新規作成&保存
    config["host"] = input("ドメインを入力してください(例:example.com)-> https:// ")
    config["i"] = input('"通知を見る"の権限を有効にしたAPIトークンを入力してください->')
    config["request_timeout"] = 10
    config["ws_reconnect_limit"] = 10
    config["log_level"] = "WARNING"
    print("初期設定が完了しました\n誤入力した/再設定をしたい場合は`config.json`を削除してください")
    json.dump(config, fp=open(file="config.json", mode="x", encoding="UTF-8"))
    log_main.info("Config file create&saved")
ws_url = f'wss://{config["host"]}/streaming?i={config["i"]}'

# 画像保存用の.dataフォルダが存在しない場合作成するように
if not os.path.exists(".data"):
    os.mkdir(".data")
    log_main.info("Create './.data' directory")

# /./data/hash.jsonが存在するかどうかの確認
if not os.path.exists(".data/hash.json"):
    open(file=".data/hash.json", mode="x", encoding="UTF-8").write("{}")
    log_main.info("Create './.data/hash.json' file")


# 生存確認
log_main.info("Connection check")
try:
    resp_code = requests.request(
        "GET", f'https://{config["host"]}', timeout=config["request_timeout"]
    ).status_code
    log_main.info("Connection check success")
except requests.exceptions.ConnectionError:
    print("サーバーへの接続ができませんでした\n入力したドメインが正しいかどうかを確認してください")
    log_main.critical("Cannot connect to server!\nlease check domain.")
    exit()
match resp_code:
    case 404:
        print(
            "API接続ができませんでした\n - 利用しているインスタンスが正常に稼働しているか\n - 入力したドメインが正しいかどうか\nを確認してください"
        )
        log_main.critical("Unable to connect to API!\nPlease check domain and token.")
        exit()
    case 410 | 500 | 502 | 503:
        print(
            "サーバーが正常に応答しませんでした\n利用しているインスタンスが正常に稼働しているかを確認してください\nStatusCode:",
            resp_code,
        )
        log_main.critical(
            "Server is not responding normally!\nPlease check instance is running.\nStatusCode:",
            resp_code,
        )
        exit()
    case 429:
        print("レートリミットに達しました\nしばらくしてから再実行してください")
        log_main.critical("Rate limit reached!\nPlease try again later.")
        exit()

log_main.info("Misskey API connection check")
try:
    mk = Misskey(config["host"], i=config["i"])
    log_main.info("Misskey API connection check success")
except requests.exceptions.ConnectionError:
    print("ドメインが違います\nconfig.jsonを削除/編集してもう一度入力しなおしてください")
    log_main.critical("Domain is wrong!\nPlease check domain.")
    exit()
except mk_exceptions.MisskeyAuthorizeFailedException:
    print("APIキーが違います\nconfig.jsonを削除/編集して入力しなおしてください")
    log_main.critical("API key is wrong!\nPlease check API key.")
    exit()
me = mk.i()


class main:
    def __init__(self) -> None:
        # self.loop = asyncio.get_event_loop()
        self.websocket_task = None
        self.icon_task = None

    @staticmethod
    def get_image(url: str | dict, name: str | None = None) -> str:
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

        print("get_img")

        # 引数urlがdictかどうか(指定されているのがユーザーのアイコンなのか)を判断
        if isinstance(url, dict):
            name = url["id"]  # 画像保存時の名前用にuidを格納
            url = url["avatarUrl"]  # 引数から画像URLを取得し再格納
        img_path = glob(f"./.data/{name}.*")
        try:
            img_data = requests.get(url, timeout=config["request_timeout"])  # type: ignore
            hash_json = json.load(open(file=".data/hash.json", mode="r"))
        except requests.exceptions.ConnectionError:
            return "icon/icon.png"
        if len(img_path) != 0:
            img_path = img_path[0]
            # ハッシュを格納しているjsonファイルをロード
            if name in hash_json:
                if hash_json[name] == sha256(img_data.content).hexdigest():
                    print("hash_return")
                    return img_path

        try:
            if img_data.status_code == 200:  # ステータスが200かどうかを確認
                with BytesIO(img_data.content) as buf:
                    img = Image.open(buf)
                    img_path = f".data/{name}.{img.format.lower()}"  # type: ignore | 返り値用の変数にパスを格納
                    img.save(img_path)  # なんやかんや保存

                hash_json[name] = sha256(img_data.content).hexdigest()
                json.dump(hash_json, open(file=".data/hash.json", mode="w"))
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

        Returns:
            None
        """

        print("notify send")
        notifier.title = title
        notifier.message = content
        notifier.icon = img
        notifier.send()

    @staticmethod
    async def websocket_connect():
        """
        websocket接続するためのやつ
        """
        while True:
            try:
                async with websockets.connect(ws_url) as ws:  # websocket接続
                    print("ws connect")
                    log_main.info("Websocket connected")
                    await ws.send(
                        json.dumps(
                            {"type": "connect", "body": {"channel": "main", "id": "1"}}
                        )  # チャンネル接続をする旨を送信
                    )
                    print("ready")
                    ws_reconnect_count = 0
                    while True:
                        recv = json.loads(await ws.recv())
                        print(recv)  # デバッグ用
                        if recv["type"] == "channel":
                            if recv["body"]["type"] == "notification":
                                recv_body = recv["body"]["body"]
                                if recv_body.get("user") is not None:
                                    name = (
                                        recv_body["user"]["name"]
                                        if recv_body["user"]["name"] is not None
                                        else recv_body["user"]["username"]
                                    )
                                match recv_body["type"]:  # 通知の種類によって動作を分ける
                                    case "reaction":  # リアクション
                                        if (
                                            re.match(r".+@", recv_body["reaction"])
                                            is not None
                                        ):
                                            emoji = re.match(
                                                r".+@", recv_body["reaction"]
                                            )
                                            title = f"{name}が{emoji.group()[1:-1]}でリアクションしました"
                                        else:
                                            emoji = recv_body["reaction"]
                                            title = f"{name}が{emoji}でリアクションしました"
                                        await main.notify_def(
                                            title=title,
                                            content=recv_body["note"]["text"],
                                            img=main.get_image(recv_body["user"]),
                                        )

                                    case "reply":  # リプライ
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
                                            title=f"{name}が返信しました",
                                            content=f'{msg}\n------------\n{recv_body["note"]["reply"]["text"]}',
                                            img=main.get_image(recv_body["user"]),
                                        )

                                    case "mention":  # メンション
                                        await main.notify_def(
                                            title=f"{name}がメンションしました",
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
                                            img=main.get_image(recv_body["user"]),
                                        )

                                    case "renote":  # リノート
                                        await main.notify_def(
                                            title=f"{name}がリノートしました",
                                            content=recv_body["note"]["renote"]["text"],
                                            img=main.get_image(recv_body["user"]),
                                        )

                                    case "quote":  # 引用リノート
                                        await main.notify_def(
                                            title=f"{name}が引用リノートしました",
                                            content=f'{recv_body["note"]["text"]}\n-------------\n{recv_body["note"]["renote"]["text"]}',
                                            img=main.get_image(recv_body["user"]),
                                        )

                                    case "follow":  # フォロー
                                        await main.notify_def(
                                            title=f'{name}@{recv_body["user"]["host"]}',
                                            content="ホョローされました",
                                            img=main.get_image(recv_body["user"]),
                                        )

                                    case "followRequestAccepted":  # フォロー承認
                                        await main.notify_def(
                                            title=f'{name}@{recv_body["user"]["host"]}',
                                            content="ホョローが承認されました",
                                            img=main.get_image(recv_body["user"]),
                                        )

                                    case "receiveFollowRequest":  # フォローリクエスト
                                        await main.notify_def(
                                            title=f'{name}@{recv_body["user"]["host"]}',
                                            content="ホョローがリクエストされました",
                                            img=main.get_image(recv_body["user"]),
                                        )

                                    case "pollEnded":  # 投票終了
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
                                            img=main.get_image(recv_body["user"]),
                                        )

                                    case "app":  # アプリ通知
                                        await main.notify_def(
                                            title=recv_body["header"],
                                            content=recv_body["body"],
                                            img=main.get_image(
                                                recv_body["icon"], recv_body["header"]
                                            ),
                                        )
                            else:
                                pass
            except websockets.exceptions.ConnectionClosedError:
                if ws_reconnect_count == config["ws_reconnect_limit"]:
                    print("websocket disconnected. reconnect limit reached.")
                    await main.notify_def(
                        title=app_name,
                        content="再接続の回数が既定の回数を超えたため中断して終了します",
                        img=app_icon,
                    )
                    return
                log_main.warning(
                    "Websocket disconnected. reconecting...\ntrials count: ",
                    ws_reconnect_count,
                )
                await main.notify_def(
                    title=app_name, content="サーバーから切断されました\n5秒後に再接続します...", img=app_icon
                )

                await asyncio.sleep(5)
                ws_reconnect_count += 1

    def stopper(self):
        """アプリ終了時に呼び出す関数"""
        log_main.info("stopper called")
        main.websocket_task.cancel()
        icon.stop()

    async def runner(self, icon):
        """
        ### アプリ起動時に呼び出されるやつ
        引数:
            icon:
        """

        self.websocket_task = asyncio.create_task(main.websocket_connect())
        log_main.info("Start websocket task")
        self.icon_task = asyncio.create_task(asyncio.to_thread(icon.run))
        log_main.info("Start icon task")

        try:
            await self.websocket_task
            await self.icon_task
        except asyncio.CancelledError:
            log_main.info("task cancelled")
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

log_main.info("Start main task...")
asyncio.run(main.runner(icon))
