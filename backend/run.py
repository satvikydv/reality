"""Dev entry point.

HTTPS (default): binds HOST (0.0.0.0) so a phone on the same Wi-Fi can use the camera.
    python scripts/make_cert.py
    python backend/run.py

Plain HTTP over USB: PLAIN_HTTP=1 binds 127.0.0.1 only (default port 8000). The phone reaches it
through `adb reverse` (scripts/usb_connect.ps1), and http://localhost is a secure context on Chrome.
    PLAIN_HTTP=1 python backend/run.py

ACCESS_TOKEN is required whenever the server is bound to anything other than loopback.
"""
from __future__ import annotations

import argparse
import ipaddress
import os
import sys
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
load_dotenv(ROOT / ".env")


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def truthy(v: str | None) -> bool:
    return (v or "").strip().lower() in ("1", "true", "yes", "on")


ap = argparse.ArgumentParser()
ap.add_argument("--http", action="store_true", help="same as PLAIN_HTTP=1")
ap.add_argument("--port", type=int)
ap.add_argument("--host")
ap.add_argument("--reload", action="store_true")
args = ap.parse_args()

plain = args.http or truthy(os.getenv("PLAIN_HTTP"))
if plain:
    # Loopback only, whatever HOST says: the USB path never needs a LAN listener.
    host = "127.0.0.1"
    if (args.host or os.getenv("HOST")) not in (None, "", host):
        print(f"PLAIN_HTTP ignores HOST/--host and binds {host} only")
    port = args.port or int(os.getenv("PLAIN_HTTP_PORT", "8000"))
else:
    host = args.host or os.getenv("HOST", "0.0.0.0")
    port = args.port or int(os.getenv("PORT", "8443"))

token = os.getenv("ACCESS_TOKEN", "").strip()
if not is_loopback(host) and not token:
    sys.exit(f"Refusing to bind {host}: set ACCESS_TOKEN (any long random string) in .env for non-loopback binds.")
# main.py reads this to decide whether requests must carry the token (unset = fail closed).
os.environ["BIND_HOST"] = host

ssl = {}
if not plain:
    cert, key = ROOT / "certs" / "cert.pem", ROOT / "certs" / "key.pem"
    if not cert.exists():
        sys.exit("No certs/. Run: python scripts/make_cert.py")
    ssl = {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}

scheme = "http" if plain else "https"
if plain:
    print(f"serving {scheme}://{host}:{port} (loopback only; phone over USB: scripts/usb_connect.ps1)")
else:
    print(f"serving {scheme}://{host}:{port}  (phone: {scheme}://<laptop-LAN-IP>:{port}/?token=<ACCESS_TOKEN>)")
uvicorn.run("app.main:app", host=host, port=port, reload=args.reload, app_dir=str(Path(__file__).resolve().parent), **ssl)
