"""FastAPI service exposing user_data/advisory_state.json (regime/directive
judgement) for the dashboard.

kfreqai.php (kurage_web) runs on a separate host (heteml) and can't read
this host's files directly. freqtrade's own REST API (18313) doesn't know
about this custom advisory concept and offers no supported plugin point for
adding routes to it without forking upstream (see project discussion,
2026-07-13), so this is a small standalone API instead.

No new externally-facing port: binds 127.0.0.1 only. It's reached through
the already-public 18314 nginx server via a path (see
/etc/nginx/conf.d/kfreqai-18314.conf, location /advisory-state), which
already forwards FreqUI traffic to 18313 -- this just adds one more path
proxied to this process instead of freqtrade.
"""
import os
import sys

from fastapi import FastAPI

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "user_data", "strategies"),
)
import advisory_state  # noqa: E402

app = FastAPI()


@app.get("/advisory-state")
def get_advisory_state():
    return advisory_state.read_state()
