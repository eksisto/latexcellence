import { test } from "node:test";
import assert from "node:assert/strict";
import crypto from "node:crypto";
import { Md5, md5Hex } from "./md5.js";

// 参考实现：Node 内置 OpenSSL MD5（与后端 hashlib.md5 同源）
const refMd5 = (buf) => crypto.createHash("md5").update(buf).digest("hex");

test("md5Hex 与参考实现对空串与 RFC 1321 标准向量一致", () => {
  const vectors = [
    Buffer.from(""),
    Buffer.from("a"),
    Buffer.from("abc"),
    Buffer.from("message digest"),
    Buffer.from("abcdefghijklmnopqrstuvwxyz"),
    Buffer.from(
      "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",
    ),
    Buffer.from(
      "12345678901234567890123456789012345678901234567890123456789012345678901234567890",
    ),
  ];
  for (const buf of vectors) {
    assert.equal(md5Hex(new Uint8Array(buf)), refMd5(buf));
  }
});

test("md5Hex 覆盖填充边界与多块长度（55/56/63/64/65/127/128 字节等）", () => {
  for (const len of [1, 31, 54, 55, 56, 57, 63, 64, 65, 119, 127, 128, 1000]) {
    const buf = crypto.randomBytes(len);
    assert.equal(md5Hex(new Uint8Array(buf)), refMd5(buf), `len=${len}`);
  }
});

test("增量 update（不规则分块边界）与一次性计算一致", () => {
  const big = crypto.randomBytes(1024 * 1024 + 77);
  const hasher = new Md5();
  let offset = 0;
  while (offset < big.length) {
    // 故意取不与 64 字节块对齐的边界
    const end = Math.min(offset + 100000 + (offset % 7), big.length);
    hasher.update(new Uint8Array(big.subarray(offset, end)));
    offset = end;
  }
  assert.equal(hasher.hex(), refMd5(big));
});

test("大输入（>64MiB，位长度高 32 位非零路径）一致", () => {
  const huge = crypto.randomBytes(70 * 1024 * 1024 + 3);
  assert.equal(md5Hex(new Uint8Array(huge)), refMd5(huge));
});
