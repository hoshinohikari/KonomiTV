export {};

// アプリとは別のページで実行し、設定同期や進捗監視による削除後の書き戻しを避ける。
const resetButton = document.querySelector<HTMLButtonElement>('#reset')!;
const status = document.querySelector<HTMLParagraphElement>('#status')!;

/** 現在のオリジンの DB を削除し、別タブで接続が残る場合は操作方法を表示する。 */
function deleteDatabase(name: string): Promise<void> {
    return new Promise((resolve, reject) => {
        const request = indexedDB.deleteDatabase(name);
        request.onsuccess = () => resolve();
        request.onerror = () => reject(request.error ?? new Error('データベースを削除できませんでした。'));
        request.onblocked = () => {
            status.textContent = 'ほかのタブやアプリのウィンドウを閉じてください。閉じると削除を続行します。';
        };
    });
}

resetButton.addEventListener('click', async () => {
    resetButton.disabled = true;
    status.textContent = 'サイトデータを削除しています…';
    try {
        // アプリが書き戻さないよう、先にバックグラウンド保存を止めて Service Worker を解除する。
        if ('serviceWorker' in navigator) {
            for (const registration of await navigator.serviceWorker.getRegistrations()) {
                if (registration.backgroundFetch !== undefined) {
                    for (const id of await registration.backgroundFetch.getIds()) {
                        await (await registration.backgroundFetch.get(id))?.abort();
                    }
                }
                await registration.unregister();
            }
        }
        // 列挙 API がないブラウザでも、既知のオフライン DB は削除する。
        const names = new Set(['KonomiTV-OfflineVideos']);
        if (typeof indexedDB.databases === 'function') {
            for (const database of await indexedDB.databases()) {
                if (database.name !== undefined) names.add(database.name);
            }
        }
        for (const name of names) await deleteDatabase(name);
        if ('caches' in window) {
            for (const name of await caches.keys()) await caches.delete(name);
        }
        // 個別設定の保存ではなく、利用者が明示的に要求したオリジン全体の初期化。
        localStorage.clear();
        sessionStorage.clear();
        // HttpOnly Cookie は JS から操作できない。現在のホストの参照可能な Cookie を期限切れにする。
        const paths = ['/', ...location.pathname.split('/').slice(0, -1).map((_, index, parts) => parts.slice(0, index + 1).join('/') || '/')];
        for (const cookie of document.cookie.split(';')) {
            const name = cookie.split('=')[0].trim();
            if (name === '') continue;
            for (const path of paths) {
                document.cookie = `${name}=; Max-Age=0; Path=${path}`;
                document.cookie = `${name}=; Max-Age=0; Path=${path}; Domain=${location.hostname}`;
            }
        }
        // JS が操作できない HttpOnly Cookie と HTTP キャッシュも、対応ブラウザではサーバーの応答で削除する。
        // API の開発用ポートを直接参照せず、現在のページと同じオリジンから応答を受け取る。
        const response = await fetch('/api/maintenance/reset-client-data', {
            method: 'POST',
            headers: {'X-KonomiTV-Reset': '1'},
            credentials: 'same-origin',
            cache: 'no-store',
        });
        if (response.status !== 204) {
            throw new Error(`ブラウザへの削除指示に失敗しました (HTTP ${response.status})。`);
        }
        // 削除したストレージを再利用しないよう、新しいドキュメントとしてホームを読み込む。
        window.location.replace('/tv/?reset=1');
    } catch (error) {
        console.error('Failed to reset site data:', error);
        status.textContent = `一部のデータを削除できませんでした。ほかのタブを閉じて再試行してください。\n${error instanceof Error ? error.message : String(error)}`;
        resetButton.disabled = false;
    }
});
