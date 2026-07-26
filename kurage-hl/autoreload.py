"""ソース変更を検知したらプロセスを終了する自動リロード。

hl_api / hl_engine はどちらも systemd で Restart=always なので、ファイルを
編集して os._exit すると systemd が新しいコードで即再起動する。これで
コード修正のたびの手動 `systemctl restart`(sudo) が不要になる。

対象: kurage-hl/ と user_data/strategies/ の *.py、および kurage-hl/.env。
起動時にmtimeのスナップショットを取り、以降変化したら終了する(起動直後に
誤爆しないよう、基準は起動時点)。
"""
import glob
import os
import threading
import time

_BASE = os.path.dirname(os.path.abspath(__file__))
_STRAT = os.path.join(_BASE, "..", "user_data", "strategies")


def _snapshot(dirs, extra_files):
    m = {}
    for d in dirs:
        for f in glob.glob(os.path.join(d, "*.py")):
            try:
                m[f] = os.path.getmtime(f)
            except OSError:
                pass
    for f in extra_files:
        try:
            m[f] = os.path.getmtime(f)
        except OSError:
            pass
    return m


def start(interval=3):
    dirs = [_BASE, _STRAT]
    extra = [os.path.join(_BASE, ".env")]
    base = _snapshot(dirs, extra)

    def loop():
        while True:
            time.sleep(interval)
            if _snapshot(dirs, extra) != base:
                print("[autoreload] source changed -> exiting for systemd restart", flush=True)
                os._exit(3)  # Restart=always が新コードで再起動する

    threading.Thread(target=loop, daemon=True).start()
