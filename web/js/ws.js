// WebSocket client: sends binary frames (flags u8 | seq u32 | client_ts f64 | JPEG), receives JSON replies.
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

  send(jpegBlob, { save = false } = {}) {
    return jpegBlob.arrayBuffer().then((buf) => {
      const out = new ArrayBuffer(13 + buf.byteLength);
      const dv = new DataView(out);
      dv.setUint8(0, save ? 1 : 0);
      dv.setUint32(1, ++this.seq);
      dv.setFloat64(5, performance.timeOrigin + performance.now());
      new Uint8Array(out, 13).set(new Uint8Array(buf));
      this.ws.send(out);
      return this.seq;
    });
  }
}
