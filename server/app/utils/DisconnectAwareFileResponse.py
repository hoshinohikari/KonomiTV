
import asyncio

from starlette.responses import FileResponse
from starlette.types import Message, Receive, Scope, Send


class DisconnectAwareFileResponse(FileResponse):
    """ 録画原画の Range 配信を切断時に停止し、ファイルを確実に閉じる。 """

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """
        Range の処理を Starlette に任せ、切断後の次の送信でファイル読み出しを終了する。

        Args:
            scope (Scope): ASGI リクエストの情報
            receive (Receive): リクエストや切断通知を受け取る関数
            send (Send): レスポンスを送信する関数

        Returns:
            None: 配信とファイルのクローズが完了した時点で終了
        """

        # HEAD はファイル本体を読まないため、切断監視を必要としない。
        if scope['method'].upper() == 'HEAD':
            await super().__call__(scope, receive, send)
            return

        class ClientDisconnected(Exception):
            """ ファイルのコンテキストを通常の例外で抜けるための内部通知。 """

        is_disconnected = False

        async def ListenForDisconnect() -> None:
            """
            シークによってキャンセルされた HTTP 接続の切断を監視する。

            Args:
                None

            Returns:
                None: 切断が通知された時点で終了
            """

            nonlocal is_disconnected
            # 本文の通知が先に届く場合もあるので、切断通知まで読み進める。
            while True:
                if (await receive())['type'] == 'http.disconnect':
                    is_disconnected = True
                    return

        async def SendWithDisconnectCheck(message: Message) -> None:
            """
            接続中だけ送信し、切断後は Starlette のファイルコンテキストを抜ける。

            Args:
                message (Message): Starlette が生成したレスポンスメッセージ

            Returns:
                None: メッセージの送信が完了した時点で終了
            """

            # 配信タスクを cancel すると非同期の close も中断されうるため、送信境界で例外を送出する。
            if is_disconnected is True:
                raise ClientDisconnected
            await send(message)

        # 上流の新しい Starlette 専用の内部 API は使わず、現行の asyncio 環境で監視する。
        disconnect_task = asyncio.create_task(ListenForDisconnect())
        is_interrupted = False
        try:
            await super().__call__(scope, receive, SendWithDisconnectCheck)
        except ClientDisconnected:
            is_interrupted = True
        finally:
            # 正常終了時にも receive() 待ちを残さず、監視タスクの終了と例外回収まで待つ。
            disconnect_task.cancel()
            await asyncio.gather(disconnect_task, return_exceptions=True)

        # 正常終了時のバックグラウンド処理は親クラスが実行するので、中断時だけ補う。
        if is_interrupted is True and self.background is not None:
            await self.background()
