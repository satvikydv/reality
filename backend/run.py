"""Dev entry point: HTTPS on 0.0.0.0 so a phone on the same Wi-Fi can use the camera.

    python backend/run.py            # needs certs/ (python scripts/make_cert.py)
    python backend/run.py --http     # plain HTTP, localhost only is camera-safe
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
load_dotenv(ROOT / ".env")

ap = argparse.ArgumentParser()
ap.add_argument("--http", action="store_true")
ap.add_argument("--port", type=int, default=int(os.getenv("PORT", "8443")))
ap.add_argument("--host", default=os.getenv("HOST", "0.0.0.0"))
ap.add_argument("--reload", action="store_true")
args = ap.parse_args()

cert, key = ROOT / "certs" / "cert.pem", ROOT / "certs" / "key.pem"
ssl = {}
if not args.http:
    if not cert.exists():
        sys.exit("No certs/. Run: python scripts/make_cert.py")
    ssl = {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}

scheme = "http" if args.http else "https"
print(f"serving {scheme}://localhost:{args.port}  (phone: {scheme}://<laptop-LAN-IP>:{args.port})")
uvicorn.run("app.main:app", host=args.host, port=args.port, reload=args.reload, app_dir=str(Path(__file__).resolve().parent), **ssl)
