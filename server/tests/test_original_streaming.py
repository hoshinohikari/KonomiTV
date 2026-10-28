
import asyncio
import pathlib
import tempfile
import unittest
from unittest.mock import AsyncMock

from starlette.background import BackgroundTask
from starlette.types import Message, Scope

from app.constants import QUALITY
from app.streams.StreamEncodingOptions import SplitQualityAndEncodingOptions
from app.utils.DisconnectAwareFileResponse import DisconnectAwareFileResponse


class OriginalQualityTests(unittest.TestCase):
    """ 原画専用の品質指定と通常エンコード品質の分離を検証する。 """

    def testOriginalBypassesEncodingOptions(self) -> None:
        """
        原画だけを専用画質として受け付け、エンコードオプションを付けない。

        Args:
            None

        Returns:
            None
        """

        quality = SplitQualityAndEncodingOptions('original')
        self.assertIsNotNone(quality)
        assert quality is not None
        self.assertEqual(quality.quality, 'original')
        self.assertEqual(quality.encoding_options.buildSuffix(), '')
        self.assertNotIn('original', QUALITY)

    def testRejectsRetiredAndSuffixedOriginalQualities(self) -> None:
        """
        廃止した source と、原画では意味を持たないエンコード指定を拒否する。

        Args:
            None

        Returns:
            None
        """

        for quality in ['source', 'source-hevc', 'original-hevc', 'original-10bit', 'original-24fps']:
            with self.subTest(quality=quality):
                self.assertIsNone(SplitQualityAndEncodingOptions(quality))

    def testEncodedQualitiesKeepLocal60fpsSettings(self) -> None:
        """
        原画の追加後も既存のエンコード品質と低解像度の 60fps 設定を維持する。

        Args:
            None

        Returns:
            None
        """

        for name, quality in QUALITY.items():
            with self.subTest(quality=name):
                parsed = SplitQualityAndEncodingOptions(name)
                self.assertIsNotNone(parsed)
                assert parsed is not None
                self.assertEqual(parsed.quality, name)
                # 1080p の通常フレームレート版以外は、ローカルで維持している 60fps 品質。
                if name not in ['1080p', '1080p-hevc']:
                    self.assertTrue(quality.is_60fps)


class OriginalFileResponseTests(unittest.IsolatedAsyncioTestCase):
    """ 旧 Starlette での Range 配信と切断後の読み出し停止を検証する。 """

    async def collectResponse(
        self,
        headers: list[tuple[bytes, bytes]] | None = None,
        method: str = 'GET',
        disconnect_after_body: bool = False,
    ) -> tuple[list[Message], AsyncMock]:
        """
        一時ファイルへの ASGI リクエストを実行し、送信メッセージを収集する。

        Args:
            headers (list[tuple[bytes, bytes]] | None): Range などの HTTP ヘッダー
            method (str): HTTP メソッド
            disconnect_after_body (bool): 本文の最初のチャンクで切断するか

        Returns:
            tuple[list[Message], AsyncMock]: 送信内容と終了後処理のモック
        """

        messages: list[Message] = []
        disconnected = asyncio.Event()
        background = AsyncMock()
        scope: Scope = {
            'type': 'http',
            'method': method,
            'headers': headers or [],
            'asgi': {'version': '3.0', 'spec_version': '2.3'},
        }

        async def Receive() -> Message:
            """ 切断まで待つ。Args: None. Returns: ASGI の切断通知。 """

            await disconnected.wait()
            return {'type': 'http.disconnect'}

        async def Send(message: Message) -> None:
            """ 送信を記録する。Args: message (Message): 通知内容。Returns: None. """

            messages.append(message)
            # シークによる fetch の中止を模擬し、サーバーが次のチャンクを読む前に通知する。
            if disconnect_after_body and message['type'] == 'http.response.body':
                disconnected.set()
                await asyncio.sleep(0)

        # Windows では開いたファイルを削除できないため、配信後の unlink でクローズも確認する。
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / 'recorded.ts'
            path.write_bytes(bytes(range(256)) * 1024)
            response = DisconnectAwareFileResponse(path, filename=path.name, background=BackgroundTask(background))
            response.chunk_size = 1024
            await asyncio.wait_for(response(scope, Receive, Send), timeout=5)
            path.unlink()

        return messages, background

    async def testSingleRangeKeepsHeadersAndExactBytes(self) -> None:
        """
        原画のシークに必要な開始位置・総サイズと、指定範囲だけの本文を返す。

        Args:
            None

        Returns:
            None
        """

        messages, background = await self.collectResponse([(b'range', b'bytes=188-375')])
        self.assertEqual(messages[0]['status'], 206)
        headers = dict(messages[0]['headers'])
        self.assertEqual(headers[b'content-range'], b'bytes 188-375/262144')
        self.assertEqual(headers[b'content-length'], b'188')
        self.assertEqual(headers[b'accept-ranges'], b'bytes')
        self.assertIn(b'recorded.ts', headers[b'content-disposition'])
        body = b''.join(message.get('body', b'') for message in messages)
        self.assertEqual(body, (bytes(range(256)) * 2)[188:376])
        background.assert_awaited_once()

    async def testUnsatisfiableRangeReturns416(self) -> None:
        """
        ファイル外のシーク位置は Starlette の標準どおり 416 を返す。

        Args:
            None

        Returns:
            None
        """

        messages, _ = await self.collectResponse([(b'range', b'bytes=999999-')])
        self.assertEqual(messages[0]['status'], 416)
        self.assertEqual(dict(messages[0]['headers'])[b'content-range'], b'bytes */262144')

    async def testDisconnectStopsRemainingRangeAndRunsCleanup(self) -> None:
        """
        大きな Range の取得を中止したら、残りを送らず終了後処理を1回だけ実行する。

        Args:
            None

        Returns:
            None
        """

        messages, background = await self.collectResponse([(b'range', b'bytes=0-')], disconnect_after_body=True)
        self.assertEqual(messages[0]['status'], 206)
        body = b''.join(message.get('body', b'') for message in messages)
        self.assertEqual(len(body), 1024)
        background.assert_awaited_once()

    async def testFullDownloadAndHeadKeepExistingBehavior(self) -> None:
        """
        通常のダウンロードと HEAD は、本文・ヘッダー・終了後処理を維持する。

        Args:
            None

        Returns:
            None
        """

        messages, background = await self.collectResponse()
        self.assertEqual(messages[0]['status'], 200)
        self.assertEqual(sum(len(message.get('body', b'')) for message in messages), 262144)
        background.assert_awaited_once()
        messages, background = await self.collectResponse(method='HEAD')
        self.assertEqual(messages[0]['status'], 200)
        self.assertEqual(dict(messages[0]['headers'])[b'content-length'], b'262144')
        self.assertEqual(sum(len(message.get('body', b'')) for message in messages), 0)
        background.assert_awaited_once()
