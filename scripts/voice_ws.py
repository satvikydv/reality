"""M0: stream the microphone (or a WAV file) to Muse Voice Transcribe realtime in ENDPOINTING mode.

Prints partial/final transcripts and measures:
  - speechEnd -> speechComplete latency (how long after you stop talking the final text arrives)
  - speechStart -> first partial
Every raw server event is saved so event/field names can be checked [VERIFY].

    python scripts/voice_ws.py --seconds 30 --lang English,Hindi
    python scripts/voice_ws.py --wav clip.wav          # 24 kHz mono 16-bit, paced in real time
    python scripts/voice_ws.py --list-devices
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import wave
from pathlib import Path

import websockets

from common import log_cost, require_key, summarize, write_results

VOICE_WS_URL = os.getenv("VOICE_WS_URL", "wss://api.meta.ai/v1/asr/realtime")
VOICE_MODEL = os.getenv("VOICE_MODEL", "muse-voice-transcribe-1.0")
RATE = 24000
CHUNK_MS = 100
CHUNK_SAMPLES = RATE * CHUNK_MS // 1000
USD_PER_HOUR = 0.18


def text_of(ev: dict) -> str:
    for k in ("text", "transcript", "result"):
        v = ev.get(k)
        if isinstance(v, str):
            return v
        if isinstance(v, dict) and isinstance(v.get("text"), str):
            return v["text"]
    return ""


async def mic_chunks(seconds: float, device):
    """Yield 100 ms PCM16 chunks from the default mic; the mic clock paces the stream in real time."""
    import sounddevice as sd

    loop = asyncio.get_running_loop()
    q: asyncio.Queue[bytes | None] = asyncio.Queue()

    def cb(indata, frames, t, status):
        if status:
            print(f"[mic] {status}")
        loop.call_soon_threadsafe(q.put_nowait, bytes(indata))

    with sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=CHUNK_SAMPLES, device=device, callback=cb):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            yield await q.get()


async def wav_chunks(path: Path):
    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == RATE and w.getnchannels() == 1 and w.getsampwidth() == 2, \
            f"need {RATE} Hz mono 16-bit WAV, got {w.getframerate()} Hz/{w.getnchannels()}ch/{8 * w.getsampwidth()}bit"
        while True:
            data = w.readframes(CHUNK_SAMPLES)
            if not data:
                return
            yield data
            await asyncio.sleep(CHUNK_MS / 1000)  # pace at real time (server rejects >5 s ahead)


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seconds", type=float, default=30)
    ap.add_argument("--lang", default="English,Hindi", help="languageBias, comma separated")
    ap.add_argument("--keywords", default="", help="comma separated boost words")
    ap.add_argument("--mode", default="ENDPOINTING", choices=["ENDPOINTING", "PUSH_TO_TALK", "DIARIZATION"])
    ap.add_argument("--wav", type=Path)
    ap.add_argument("--device", default=None, help="sounddevice device index or name")
    ap.add_argument("--list-devices", action="store_true")
    args = ap.parse_args()

    if args.list_devices:
        import sounddevice as sd

        print(sd.query_devices())
        return
    device = int(args.device) if args.device and args.device.isdigit() else args.device

    hello = {
        "authorization": {"accessToken": f"Bearer {require_key()}"},
        "audioEncoding": "PCM_24KHZ",
        "model": VOICE_MODEL,
        "mode": args.mode,
        "partialMode": "CUMULATIVE",
    }
    if args.lang:
        hello["languageBias"] = [s.strip() for s in args.lang.split(",") if s.strip()]
    if args.keywords:
        hello["keywords"] = [s.strip() for s in args.keywords.split(",") if s.strip()]

    events: list[dict] = []
    t_start = time.monotonic()
    marks: dict[str, float] = {}
    lat_end_to_final: list[float] = []
    lat_start_to_partial: list[float] = []
    finals: list[str] = []
    awaiting_partial = False

    async with websockets.connect(VOICE_WS_URL, max_size=None) as ws:
        t_conn = time.monotonic()
        await ws.send(json.dumps(hello))
        ack = json.loads(await asyncio.wait_for(ws.recv(), 10))
        print(f"ack after {1000 * (time.monotonic() - t_conn):.0f} ms: {ack}")
        if "sessionId" not in ack:
            print("No sessionId in first message. Handshake shape may need adjusting [VERIFY]:", json.dumps({k: v for k, v in hello.items() if k != 'authorization'}))
            return

        async def reader():
            nonlocal awaiting_partial
            try:
                async for msg in ws:
                    now = time.monotonic()
                    try:
                        ev = json.loads(msg)
                    except Exception:  # noqa: BLE001
                        print("non-JSON frame", msg[:100])
                        continue
                    ev["_t"] = round(now - t_start, 3)
                    events.append(ev)
                    kind = ev.get("type") or ev.get("event") or next((k for k in ev if k not in ("_t", "turnId")), "?")
                    if kind == "speechStart":
                        marks["start"] = now
                        awaiting_partial = True
                        print("\n[speech start]")
                    elif kind == "transcript":
                        if awaiting_partial and "start" in marks:
                            lat_start_to_partial.append((now - marks["start"]) * 1000)
                            awaiting_partial = False
                        print(f"\r  partial: {text_of(ev)}", end="", flush=True)
                    elif kind == "speechEnd":
                        marks["end"] = now
                    elif kind == "speechComplete":
                        if "end" in marks:
                            lat_end_to_final.append((now - marks["end"]) * 1000)
                        finals.append(text_of(ev))
                        print(f"\n  FINAL  : {text_of(ev)}   (turn {ev.get('turnId')})")
                    else:
                        print(f"\n  event  : {ev}")
            except websockets.ConnectionClosed as e:
                print(f"\nconnection closed {e.code} {e.reason}")

        rtask = asyncio.create_task(reader())
        src = wav_chunks(args.wav) if args.wav else mic_chunks(args.seconds, device)
        if not args.wav:
            print(f"Listening for {args.seconds:.0f}s. Speak commands, pause between them...")
        sent = 0
        async for chunk in src:
            await ws.send(chunk)
            sent += len(chunk)
        await ws.send(json.dumps({"type": "endStream"}))
        try:
            await asyncio.wait_for(rtask, 15)
        except asyncio.TimeoutError:
            print("\nserver did not close within 15 s of endStream")

    audio_s = sent / (2 * RATE)
    cost = audio_s / 3600 * USD_PER_HOUR
    log_cost("voice_ws", cost, f"{audio_s:.0f}s audio")
    print("\n### Voice realtime\n")
    print(f"- audio sent: {audio_s:.1f}s, finals: {len(finals)}")
    print(f"- speechStart -> first partial (ms): {summarize(lat_start_to_partial)}")
    print(f"- speechEnd -> speechComplete (ms): {summarize(lat_end_to_final)}")
    kinds = sorted({str(e.get('type') or e.get('event') or [k for k in e if k != '_t']) for e in events})
    print(f"- event kinds seen: {kinds}")
    out = write_results("voice_ws", {"hello": {k: v for k, v in hello.items() if k != "authorization"}, "ack": ack, "events": events,
                                     "start_to_partial_ms": lat_start_to_partial, "end_to_final_ms": lat_end_to_final})
    print(f"raw events: {out}")


if __name__ == "__main__":
    asyncio.run(main())
