import asyncio
import json
import os
import re
from glob import glob
from hashlib import sha256
from pathlib import Path
from io import BytesIO
from sys import exit
import logging

import pystray
import requests
import websockets
from misskey import Misskey
from misskey import exceptions as mk_exceptions
from win11toast import toast_async as toast
from PIL import Image


app_name = "Misskey-Notify-Client"
app_icon = "./icon/icon.png"

# ignore_events = ['unreadNotification', 'readAllNotifications', 'unreadMention', 'readAllUnreadMentions', 'unreadSpecifiedNote', 'readAllUnreadSpecifiedNotes', 'unreadMessagingMessage', 'readAllMessagingMessages']


ws_reconnect_count = 0

# ./config.jsonが存在するかどうかの確認
if os.path.exists("config.json"):
    config = json.load(open(file="config.json", mode="r", encoding="UTF-8"))
    # 存在する場合openして中身を変数に格納しつつログの設定
    match config["log_level"]:
        case "DEBUG":
            log_level = logging.DEBUG
        case "INFO":
            log_level = logging.INFO
        case "WARNING":
            log_level = logging.WARNING
        case "ERROR":
            log_level = logging.ERROR
        case "CRITICAL":
            log_level = logging.CRITICAL
        case _:
            log_level = logging.WARNING
    logging.basicConfig(
        format="%(asctime)s %(name)s - %(levelname)s: %(message)s",  # 出力のフォーマット
        datefmt="[%Y-%m-%dT%H:%M:%S%z]",  # 時間(asctime)のフォーマット
        filename="./latest.log",
        filemode="w",
        encoding="UTF-8",
        level=log_level,
    )
    log_main = logging.getLogger("main")
    log_img = logging.getLogger("img_get")
    log_notify = logging.getLogger("notifier")
    log_main.info("Config loaded")
else:
    # config.json作成とともにログの設定
    config = {}  # 存在しない場合インスタンスドメイン+トークンを聞きconfig.jsonを新規作成&保存
    config["host"] = input("ドメインを入力してください(例:example.com)-> https:// ")
    config["i"] = input('"通知を見る"の権限を有効にしたAPIトークンを入力してください->')
    config["request_timeout"] = 10
    config["ws_reconnect_limit"] = 10
    config["log_level"] = "WARNING"
    print("初期設定が完了しました\n誤入力した/再設定をしたい場合は`config.json`を削除してください")
    json.dump(config, fp=open(file="config.json", mode="x", encoding="UTF-8"))
    logging.basicConfig(
        format="%(asctime)s %(name)s - %(levelname)s: %(message)s",  # 出力のフォーマット
        datefmt="[%Y-%m-%dT%H:%M:%S%z]",  # 時間(asctime)のフォーマット
        filename="./latest.log",
        filemode="w",
        encoding="UTF-8",
        level=logging.WARNING,
    )
    log_main = logging.getLogger("main")
    log_img = logging.getLogger("img_get")
    log_notify = logging.getLogger("notifier")
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
    log_main.critical("Cannot connect to server! Please check domain.")
    exit()
match resp_code:
    case 404:
        print(
            "API接続ができませんでした\n - 利用しているインスタンスが正常に稼働しているか\n - 入力したドメインが正しいかどうか\nを確認してください"
        )
        log_main.critical("Unable to connect to API! Please check domain and token.")
        exit()
    case 410 | 500 | 502 | 503:
        print(
            "サーバーが正常に応答しませんでした\n利用しているインスタンスが正常に稼働しているかを確認してください\nStatusCode:",
            resp_code,
        )
        log_main.critical(
            "Server is not responding normally! Please check instance is running. StatusCode:",
            resp_code,
        )
        exit()
    case 429:
        print("レートリミットに達しました\nしばらくしてから再実行してください")
        log_main.critical("Rate limit reached! Please try again later.")
        exit()

log_main.info("Misskey API connection check")
try:
    mk = Misskey(config["host"], i=config["i"])
    log_main.info("Misskey API connection check success")
except requests.exceptions.ConnectionError:
    print("ドメインが違います\nconfig.jsonを削除/編集してもう一度入力しなおしてください")
    log_main.critical("Domain is wrong! Please check domain.")
    exit()
except mk_exceptions.MisskeyAuthorizeFailedException:
    print("APIキーが違います\nconfig.jsonを削除/編集して入力しなおしてください")
    log_main.critical("API key is wrong! Please check API key.")
    exit()
try:
    me = mk.i()
except requests.exceptions.JSONDecodeError:
    print("サーバー接続時にエラーが発生しました\nドメイン/APIキーが正しいかどうか確認してください")
    log_main.critical("Cannot connect to server! Please check domain/API key.")
    exit()


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

        log_img.info("get_image called")

        # 引数urlがdictかどうか(指定されているのがユーザーのアイコンなのか)を判断
        if isinstance(url, dict):
            log_img.debug("url is dict")
            name = url["id"]  # 画像保存時の名前用にuidを格納
            url = url["avatarUrl"]  # 引数から画像URLを取得し再格納
        img_path = glob(f"./.data/{name}.*")
        try:
            img_data = requests.get(url, timeout=config["request_timeout"])  # type: ignore
            hash_json = json.load(open(file=".data/hash.json", mode="r"))
        except requests.exceptions.ConnectionError:
            log_img.warning("request failed")
            return "icon/icon.png"
        if len(img_path) != 0:
            img_path = img_path[0]
            # ハッシュを格納しているjsonファイルをロード
            if name in hash_json:
                if hash_json[name] == sha256(img_data.content).hexdigest():
                    log_img.info("Connection error: No image downloaded")
                    return img_path

        try:
            if img_data.status_code == 200:  # ステータスが200かどうかを確認
                with BytesIO(img_data.content) as buf:
                    img = Image.open(buf)
                    img_path = f".data/{name}.{img.format.lower()}"  # type: ignore | 返り値用の変数にパスを格納
                    img.save(img_path)  # なんやかんや保存
                    log_img.info("Image saved")

                hash_json[name] = sha256(img_data.content).hexdigest()
                json.dump(hash_json, open(file=".data/hash.json", mode="w"))
                log_img.info("Hash saved")
            else:
                log_img.info("StatusCode error: No image downloaded")
                img_path = "./icon/icon.png"  # 返り値用の変数にアプリアイコンのパスを格納
        except requests.exceptions.ConnectionError:
            log_img.info("Connection error: No image downloaded")
            img_path = "./icon/icon.png"  # 返り値用の変数にアプリアイコンのパスを格納

        log_img.info("return")
        return img_path  # みんな大好きreturn

    @staticmethod
    async def notify_def(
        title: str,
        content: str,
        icon: str,
        icon_square: bool = False,
        open_url: str | None = None,
    ) -> None:
        """
        通知を送信するための関数

        Args:
            title (str): 通知のタイトル
            content (str): 通知の内容
            img (str): 通知に表示する画像のパス
            img_square (bool, optional): 通知に表示する画像を正方形にするかどうか
            open_url (str | None, optional): 通知をクリックしたときに開くURL

        Returns:
            None
        """

        log_notify.info("notify_def called")
        if icon_square:
            icon = {"src": f"{str(Path(icon).resolve())}", "placement": "appLogoOverride"}  # type: ignore
        else:
            icon = str(Path(icon).resolve())  # type: ignore
        result = await toast(app_id=app_name, title=title, body=content, icon=icon)
        log_notify.debug(result)

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
                    log_main.info("Send channel connection payload")
                    print("ready")
                    log_main.info("ready")
                    ws_reconnect_count = 0
                    while True:
                        recv = json.loads(await ws.recv())
                        log_main.info("payload received")
                        log_main.debug(recv)  # デバッグ用
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
                                        log_main.debug("Type: reaction")
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
                                            icon=main.get_image(recv_body["user"]),
                                        )

                                    case "reply":  # リプライ
                                        log_main.debug("Type: reply")
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
                                            icon=main.get_image(recv_body["user"]),
                                        )

                                    case "mention":  # メンション
                                        log_main.debug("Type: mention")
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
                                            icon=main.get_image(recv_body["user"]),
                                        )

                                    case "renote":  # リノート
                                        log_main.debug("Type: renote")
                                        await main.notify_def(
                                            title=f"{name}がリノートしました",
                                            content=recv_body["note"]["renote"]["text"],
                                            icon=main.get_image(recv_body["user"]),
                                        )

                                    case "quote":  # 引用リノート
                                        log_main.debug("Type: quote")
                                        await main.notify_def(
                                            title=f"{name}が引用リノートしました",
                                            content=f'{recv_body["note"]["text"]}\n-------------\n{recv_body["note"]["renote"]["text"]}',
                                            icon=main.get_image(recv_body["user"]),
                                        )

                                    case "follow":  # フォロー
                                        log_main.debug("Type: follow")
                                        await main.notify_def(
                                            title=f'{name}@{recv_body["user"]["host"]}',
                                            content="ホョローされました",
                                            icon=main.get_image(recv_body["user"]),
                                        )

                                    case "followRequestAccepted":  # フォロー承認
                                        log_main.debug("Type: followRequestAccepted")
                                        await main.notify_def(
                                            title=f'{name}@{recv_body["user"]["host"]}',
                                            content="ホョローが承認されました",
                                            icon=main.get_image(recv_body["user"]),
                                        )

                                    case "receiveFollowRequest":  # フォローリクエスト
                                        log_main.debug("Type: receiveFollowRequest")
                                        await main.notify_def(
                                            title=f'{name}@{recv_body["user"]["host"]}',
                                            content="ホョローがリクエストされました",
                                            icon=main.get_image(recv_body["user"]),
                                        )

                                    case "pollEnded":  # 投票終了
                                        log_main.debug("Type: pollEnded")
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
                                            icon=main.get_image(recv_body["user"]),
                                        )

                                    case "app":  # アプリ通知
                                        log_main.debug("Type: app")
                                        await main.notify_def(
                                            title=recv_body["header"],
                                            content=recv_body["body"],
                                            icon=main.get_image(
                                                recv_body["icon"], recv_body["header"]
                                            ),
                                        )
                            else:
                                pass
            except websockets.exceptions.ConnectionClosedError:
                if ws_reconnect_count == config["ws_reconnect_limit"]:
                    print("websocket disconnected. reconnect limit reached.")
                    log_main.critical(
                        "websocket disconnected. reconnect limit reached."
                    )
                    await main.notify_def(
                        title=app_name,
                        content="再接続の回数が既定の回数を超えたため中断して終了します",
                        icon=app_icon,
                    )
                    return
                log_main.warning(
                    "Websocket disconnected. reconecting...\ntrials count: ",
                    ws_reconnect_count,
                )
                await main.notify_def(
                    title=app_name,
                    content="サーバーから切断されました\n5秒後に再接続します...",
                    icon=app_icon,
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
    asyncio.run(main.notify_def(title=app_name, content=message, icon=app_icon))


icon = pystray.Icon(
    "Misskey-notify-client",
    icon=Image.open(app_icon),
    menu=pystray.Menu(
        pystray.MenuItem("すべて既読にする", notify_read, checked=None),
        pystray.MenuItem("終了", main.stopper, checked=None),
    ),
)
# TODO: どの通知受け取るか設定できるように


log_main.debug("test")
log_main.info("test")
log_main.warning("test")
log_main.error("test")
log_main.critical("test")


print("client_startup...")
# icon_thread = threading.Thread(target=icon.run).start()
print("icon starting...")

log_main.info("Start main task...")
asyncio.run(main.runner(icon))
