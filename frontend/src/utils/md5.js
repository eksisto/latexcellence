/**
 * 增量 MD5（分片上传 v2 P5）
 *
 * 用途：
 * - 上传前对每个文件计算全量 MD5（写入 manifest，服务端 complete 全量复核）；
 * - 上传时对每个分片切片计算 MD5，作为 X-Part-MD5 请求头（服务端逐片校验）。
 *
 * 浏览器 WebCrypto 出于安全设计不提供 MD5，这里实现纯 JS 的 RFC 1321 算法
 * （Uint8Array 输入、64 字节块流式 update、hex() 定稿），避免为此引入额外
 * npm 依赖。仅用于传输完整性校验，非安全散列场景。
 */

// 每轮移位量表（RFC 1321）
const SHIFTS = [7, 12, 17, 22, 5, 9, 14, 20, 4, 11, 16, 23, 6, 10, 15, 21];

// K[i] = floor(|sin(i + 1)| × 2^32)
const K = new Int32Array(64);
for (let i = 0; i < 64; i++) {
  K[i] = Math.floor(Math.abs(Math.sin(i + 1)) * 4294967296);
}

// 块工作缓冲区：processBlock 为纯同步计算、实例间不会交错执行，
// 模块级复用一份即可。避免每 64 字节块一次 Int32Array 分配——
// 大文件全量哈希时每 MB 约 1.6 万次分配的 GC 压力不可忽视。
const M = new Int32Array(16);

function rotl(x, c) {
  return (x << c) | (x >>> (32 - c));
}

/** 32 位字按小端序输出 8 个 hex 字符 */
function wordToHexLE(word) {
  let s = "";
  for (let i = 0; i < 4; i++) {
    s += ((word >>> (i * 8)) & 0xff).toString(16).padStart(2, "0");
  }
  return s;
}

export class Md5 {
  constructor() {
    this.a = 0x67452301;
    this.b = 0xefcdab89;
    this.c = 0x98badcfe;
    this.d = 0x10325476;
    this.totalBytes = 0;
    this.buffer = new Uint8Array(64);
    this.bufLen = 0;
  }

  /** 处理单个 64 字节块（data 从 offset 起；写入共享缓冲区 M） */
  processBlock(data, offset) {
    for (let i = 0; i < 16; i++) {
      const j = offset + i * 4;
      M[i] =
        data[j] |
        (data[j + 1] << 8) |
        (data[j + 2] << 16) |
        (data[j + 3] << 24);
    }
    let a = this.a;
    let b = this.b;
    let c = this.c;
    let d = this.d;
    for (let i = 0; i < 64; i++) {
      let f;
      let g;
      if (i < 16) {
        f = (b & c) | (~b & d);
        g = i;
      } else if (i < 32) {
        f = (d & b) | (~d & c);
        g = (5 * i + 1) % 16;
      } else if (i < 48) {
        f = b ^ c ^ d;
        g = (3 * i + 5) % 16;
      } else {
        f = c ^ (b | ~d);
        g = (7 * i) % 16;
      }
      const tmp = d;
      d = c;
      c = b;
      b =
        (b + rotl((a + f + K[i] + M[g]) | 0, SHIFTS[(i >> 4) * 4 + (i & 3)])) |
        0;
      a = tmp;
    }
    this.a = (this.a + a) | 0;
    this.b = (this.b + b) | 0;
    this.c = (this.c + c) | 0;
    this.d = (this.d + d) | 0;
  }

  /** 追加一段字节（可多次调用以流式计算大文件的 MD5） */
  update(data) {
    const len = data.length;
    this.totalBytes += len;
    let offset = 0;
    if (this.bufLen > 0) {
      const take = Math.min(64 - this.bufLen, len);
      this.buffer.set(data.subarray(0, take), this.bufLen);
      this.bufLen += take;
      offset = take;
      if (this.bufLen === 64) {
        this.processBlock(this.buffer, 0);
        this.bufLen = 0;
      }
    }
    while (offset + 64 <= len) {
      this.processBlock(data, offset);
      offset += 64;
    }
    if (offset < len) {
      this.buffer.set(data.subarray(offset), 0);
      this.bufLen = len - offset;
    }
    return this;
  }

  /** 定稿并返回 32 位小写 hex。调用后本实例状态被消耗，不可再 update。 */
  hex() {
    const bitLength = this.totalBytes * 8;
    // padding：0x80 后补 0 至 ≡56 (mod 64)，再追加 64 位小端比特长度
    const tailLen = this.bufLen + 72;
    const tail = new Uint8Array(tailLen);
    tail.set(this.buffer.subarray(0, this.bufLen));
    tail[this.bufLen] = 0x80;
    const zeroPad =
      this.bufLen + 1 <= 56 ? 56 - (this.bufLen + 1) : 120 - (this.bufLen + 1);
    const lenOffset = this.bufLen + 1 + zeroPad;
    // 位长度低/高 32 位（小端）
    tail[lenOffset] = bitLength & 0xff;
    tail[lenOffset + 1] = (bitLength >>> 8) & 0xff;
    tail[lenOffset + 2] = (bitLength >>> 16) & 0xff;
    tail[lenOffset + 3] = (bitLength >>> 24) & 0xff;
    tail[lenOffset + 4] = (bitLength / 4294967296) & 0xff;
    const blocks = lenOffset + 8;
    for (let off = 0; off < blocks; off += 64) {
      this.processBlock(tail, off);
    }
    return (
      wordToHexLE(this.a) +
      wordToHexLE(this.b) +
      wordToHexLE(this.c) +
      wordToHexLE(this.d)
    );
  }
}

/** 一次性计算 Uint8Array 的 MD5 hex */
export function md5Hex(bytes) {
  return new Md5().update(bytes).hex();
}

/** 计算 Blob（File 切片等）的 MD5 hex */
export async function md5HexFromBlob(blob) {
  const buf = await blob.arrayBuffer();
  return md5Hex(new Uint8Array(buf));
}
