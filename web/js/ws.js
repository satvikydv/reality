// WebSocket client: sends binary frames (flags u8 | seq u32 | client_ts f64 | meta_len u16 | meta JSON | JPEG), receives JSON replies.
export class FrameSocket {
  constructor(onReply, onState) {
    this.onReply = onReply;
    this.onState = onState;
    this.seq = 0;
    this.ws = null;
    this.open();
  }

  open() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    this.ws = new WebSocket(`${proto}://${location.host}/ws`);
    this.ws.binaryType = "arraybuffer";
    this.ws.onopen = () => this.onState("connected");
    this.ws.onclose = () => {
      this.onState("disconnected, retrying");
      setTimeout(() => this.open(), 1000);
    };
    this.ws.onmessage = (e) => this.onReply(JSON.parse(e.data), performance.timeOrigin + performance.now());
  }

  get ready() {
    return this.ws && this.ws.readyState === WebSocket.OPEN;
  }

  get backlog() {
    return this.ws ? this.ws.bufferedAmount : 0;
  }

  // meta: optional JSON-serialisable object stored with the frame (e.g. orientation).
  send(jpegBlob, { save = false, meta = null } = {}) {
    return jpegBlob.arrayBuffer().then((buf) => {
      const m = meta ? new TextEncoder().encode(JSON.stringify(meta)) : new Uint8Array(0);
      const out = new ArrayBuffer(15 + m.length + buf.byteLength);
      const dv = new DataView(out);
      dv.setUint8(0, save ? 1 : 0);
      dv.setUint32(1, ++this.seq);
      dv.setFloat64(5, performance.timeOrigin + performance.now());
      dv.setUint16(13, m.length);
      new Uint8Array(out, 15, m.length).set(m);
      new Uint8Array(out, 15 + m.length).set(new Uint8Array(buf));
      this.ws.send(out);
      return this.seq;
    });
  }
}
