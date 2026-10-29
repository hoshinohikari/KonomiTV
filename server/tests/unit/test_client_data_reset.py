import httpx2 as httpx
from fastapi import FastAPI

from app.routers.MaintenanceRouter import router


async def testClientDataResetRequiresExplicitSameOriginRequest() -> None:
    """
    同一オリジンの明示的な POST のみが、データ削除指示を受け取れることを確認する。

    Args:
        None

    Returns:
        None
    """

    # 実際のサーバーやブラウザを使わず、応答ヘッダーとリクエスト境界を検証する。
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://reset.example.test') as client:
        for headers in [
            {},
            {'Origin': 'https://reset.example.test'},
            {'Origin': 'https://other.example.test', 'X-KonomiTV-Reset': '1'},
            {'Origin': 'https://reset.example.test:7001', 'X-KonomiTV-Reset': '1'},
            {'Origin': 'https://[invalid', 'X-KonomiTV-Reset': '1'},
        ]:
            response = await client.post('/api/maintenance/reset-client-data', headers=headers)
            assert response.status_code == 403
            assert 'clear-site-data' not in response.headers

        response = await client.get('/api/maintenance/reset-client-data')
        assert response.status_code == 405
        assert 'clear-site-data' not in response.headers

        response = await client.post('/api/maintenance/reset-client-data', headers={
            'Origin': 'https://reset.example.test', 'X-KonomiTV-Reset': '1',
        })
        assert response.status_code == 204
        assert response.content == b''
        assert response.headers['clear-site-data'] == '"cache", "cookies", "storage"'
        assert response.headers['cache-control'] == 'no-store'
