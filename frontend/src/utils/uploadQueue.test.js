import { test } from "node:test";
import assert from "node:assert/strict";

import {
  MAX_PART_ATTEMPTS,
  UPLOAD_CONCURRENCY,
  createUploadQueue,
  isPausedError,
  isRetryablePartError,
  planTasksFromStatus,
  resetForResume,
  runUploadQueue,
} from "./uploadQueue.js";

// ── 测试脚手架 ────────────────────────────────────────────────────────

const CHUNK = 100;

/** 构造 n 个连续分片任务（每片 CHUNK 字节，单片文件 fi=0） */
function makeTasks(n) {
  return Array.from({ length: n }, (_, ci) => ({
    fi: 0,
    ci,
    bytes: CHUNK,
    offset: ci * CHUNK,
  }));
}

function makeQueue(totalBytes) {
  return createUploadQueue({
    sessionId: "sess-1",
    kind: "zip",
    name: "demo.zip",
    totalBytes,
    chunkPartBytes: CHUNK,
    files: [{ file: null, size: totalBytes }],
  });
}

function abortError() {
  const err = new Error("This operation was aborted");
  err.name = "AbortError";
  return err;
}

async function waitUntil(cond, timeoutMs = 2000) {
  const deadline = Date.now() + timeoutMs;
  while (!cond()) {
    if (Date.now() > deadline) throw new Error("waitUntil 超时");
    await new Promise((r) => setTimeout(r, 1));
  }
}

// ── 纯函数：建队 / 位图对位 / 错误分类 ────────────────────────────────

test("createUploadQueue 构建初始队列：未暂停、无致命错误、未在运行", () => {
  const q = makeQueue(300);
  assert.equal(q.sessionId, "sess-1");
  assert.equal(q.totalBytes, 300);
  assert.equal(q.chunkPartBytes, CHUNK);
  assert.equal(q.uploadedBytes, 0);
  assert.equal(q.paused, false);
  assert.equal(q.fatalError, null);
  assert.equal(q.running, false);
  assert.ok(q.inflight instanceof Set);
  assert.equal(q.inflight.size, 0);
});

test("planTasksFromStatus 按位图重建已收字节基线与缺失任务列表", () => {
  const st = {
    chunk_size: 100,
    files: [
      // 前两片已收（200 字节），尾部余数片缺失
      { index: 0, size: 250, bitmap: [true, true, false] },
      // 整个文件缺失
      { index: 1, size: 80, bitmap: [false] },
    ],
  };
  const { receivedBytes, tasks } = planTasksFromStatus(st, 8 * 1024 * 1024);
  assert.equal(receivedBytes, 200);
  assert.deepEqual(tasks, [
    { fi: 0, ci: 2, bytes: 50, offset: 200 },
    { fi: 1, ci: 0, bytes: 80, offset: 0 },
  ]);
});

test("planTasksFromStatus 缺省 chunk_size 时回退调用方分片大小", () => {
  const st = { files: [{ index: 0, size: 30, bitmap: [false, false, false] }] };
  const { receivedBytes, tasks } = planTasksFromStatus(st, 10);
  assert.equal(receivedBytes, 0);
  assert.deepEqual(tasks, [
    { fi: 0, ci: 0, bytes: 10, offset: 0 },
    { fi: 0, ci: 1, bytes: 10, offset: 10 },
    { fi: 0, ci: 2, bytes: 10, offset: 20 },
  ]);
});

test("isRetryablePartError：无错误码与分片 MD5 不符可重试，其余为致命", () => {
  assert.equal(isRetryablePartError(new Error("network down")), true);
  assert.equal(isRetryablePartError({ code: "part_md5_mismatch" }), true);
  assert.equal(isRetryablePartError({ code: "part_index_invalid" }), false);
});

// ── 状态机：正常完成 / 并发上限 / 重试 ────────────────────────────────

test("正常完成：消费全部任务、累计进度并回调 onComplete", async () => {
  const q = makeQueue(CHUNK * 5);
  const uploaded = [];
  let completed = 0;
  await runUploadQueue(q, makeTasks(5), {
    uploadPart: async (queue, task) => {
      uploaded.push(task.ci);
    },
    onProgress: () => {},
    onComplete: () => {
      completed++;
    },
  });
  assert.deepEqual(
    uploaded.sort((a, b) => a - b),
    [0, 1, 2, 3, 4],
    "全部分片恰好被消费一次",
  );
  assert.equal(q.uploadedBytes, CHUNK * 5);
  assert.equal(completed, 1);
  assert.equal(q.fatalError, null);
  assert.equal(q.paused, false);
  assert.equal(q.inflight.size, 0);
});

test("并发受 UPLOAD_CONCURRENCY 上限约束", async () => {
  const q = makeQueue(CHUNK * 10);
  let active = 0;
  let peak = 0;
  await runUploadQueue(q, makeTasks(10), {
    uploadPart: async () => {
      active++;
      peak = Math.max(peak, active);
      await new Promise((r) => setTimeout(r, 2));
      active--;
    },
  });
  assert.equal(peak, Math.min(UPLOAD_CONCURRENCY, 10));
  assert.equal(active, 0);
});

test("可重试失败（无错误码）按指数退避重试直至成功", async () => {
  const q = makeQueue(CHUNK);
  let attempts = 0;
  await runUploadQueue(q, makeTasks(1), {
    retryBaseMs: 1,
    uploadPart: async () => {
      attempts++;
      if (attempts < 3) throw new Error("network hiccup");
    },
  });
  assert.equal(attempts, 3);
  assert.equal(q.uploadedBytes, CHUNK);
  assert.equal(q.fatalError, null);
});

test("不可重试错误不重试、立即记入 fatalError 并上抛", async () => {
  const q = makeQueue(CHUNK);
  const bad = new Error("bad part");
  bad.code = "part_index_invalid";
  let attempts = 0;
  await assert.rejects(
    runUploadQueue(q, makeTasks(1), {
      retryBaseMs: 1,
      uploadPart: async () => {
        attempts++;
        throw bad;
      },
    }),
    (err) => err === bad,
  );
  assert.equal(attempts, 1);
  assert.equal(q.fatalError, bad);
});

// ── Required 缺陷回归：fatal 后 resume 清空标志并可继续消费 ──────────

test("断点续传回归：fatal 后 resetForResume 清空标记，恢复可继续消费剩余任务", async () => {
  const q = makeQueue(CHUNK * 3);
  const tasks = makeTasks(3);

  // 第一次运行：网络持续失败，单个分片重试 MAX_PART_ATTEMPTS 后致命退出
  const netDown = new Error("network down");
  let attempts = 0;
  await assert.rejects(
    runUploadQueue(q, tasks, {
      concurrency: 1,
      retryBaseMs: 1,
      uploadPart: async () => {
        attempts++;
        throw netDown;
      },
    }),
    (err) => err === netDown,
  );
  assert.equal(attempts, MAX_PART_ATTEMPTS);
  assert.equal(q.fatalError, netDown);
  assert.equal(q.uploadedBytes, 0);

  // 复现旧缺陷机理：不清 fatal 直接重入 → workers 跳过全部任务，
  // 仅原地重抛陈旧错误、零上传动作（修复前的「继续上传」行为）
  let staleCalls = 0;
  await assert.rejects(
    runUploadQueue(q, tasks, {
      uploadPart: async () => {
        staleCalls++;
      },
    }),
    (err) => err === netDown,
  );
  assert.equal(staleCalls, 0);

  // 「继续上传」路径：复位清空致命标志后按位图补传全部缺失分片
  resetForResume(q);
  assert.equal(q.fatalError, null);
  assert.equal(q.paused, false);
  assert.equal(q.running, true);

  const uploaded = [];
  let completed = 0;
  await runUploadQueue(q, tasks, {
    uploadPart: async (queue, task) => {
      uploaded.push(task.ci);
    },
    onComplete: () => {
      completed++;
    },
  });
  assert.deepEqual(
    uploaded.sort((a, b) => a - b),
    [0, 1, 2],
  );
  assert.equal(q.uploadedBytes, CHUNK * 3);
  assert.equal(completed, 1);
  assert.equal(q.fatalError, null);
});

test("complete 阶段失败按致命处理，复位后可重新走完完成流程", async () => {
  const q = makeQueue(CHUNK);
  const fail = new Error("complete failed");
  await assert.rejects(
    runUploadQueue(q, makeTasks(1), {
      uploadPart: async () => {},
      onComplete: () => {
        throw fail;
      },
    }),
    (err) => err === fail,
  );
  assert.equal(q.fatalError, fail);
  assert.equal(q.uploadedBytes, CHUNK, "分片已收齐，仅落库失败");

  resetForResume(q);
  let completed = 0;
  await runUploadQueue(q, [], {
    uploadPart: async () => {},
    onComplete: () => {
      completed++;
    },
  });
  assert.equal(completed, 1);
  assert.equal(q.fatalError, null);
});

// ── 状态机：pause/resume 往返 ─────────────────────────────────────────

test("pause/resume 往返：abort 在途分片进入暂停，复位后继续消费剩余任务", async () => {
  const q = makeQueue(CHUNK * 3);
  const tasks = makeTasks(3);
  const uploaded = [];
  const release = new Map();
  let started = 0;

  // 可门控的分片上传：挂起直到放行；abort 信号触发即抛 AbortError
  const gatedUpload = (queue, task, signal) =>
    new Promise((resolve, reject) => {
      started++;
      if (signal.aborted) {
        reject(abortError());
        return;
      }
      const onAbort = () => reject(abortError());
      signal.addEventListener("abort", onAbort, { once: true });
      release.set(task.ci, () => {
        signal.removeEventListener("abort", onAbort);
        uploaded.push(task.ci);
        resolve();
      });
    });

  const run = runUploadQueue(q, tasks, { uploadPart: gatedUpload });
  await waitUntil(() => started >= tasks.length);

  // 先放行一片完成（保留部分进度），随后模拟 togglePause：
  // 置 paused 并 abort 其余在途请求
  release.get(1)();
  await waitUntil(() => uploaded.includes(1));
  q.paused = true;
  q.inflight.forEach((c) => c.abort());

  await assert.rejects(run, (err) => isPausedError(err));
  assert.equal(q.paused, true);
  assert.equal(q.fatalError, null, "暂停不得被记为致命错误");
  assert.equal(q.inflight.size, 0);
  assert.equal(q.uploadedBytes, CHUNK);

  // 「继续上传」：复位运行标志后按剩余缺失任务续跑至完成
  resetForResume(q);
  const remaining = tasks.filter((t) => !uploaded.includes(t.ci));
  await runUploadQueue(q, remaining, {
    uploadPart: async (queue, task) => {
      uploaded.push(task.ci);
    },
  });
  assert.deepEqual(
    uploaded.sort((a, b) => a - b),
    [0, 1, 2],
  );
  assert.equal(q.uploadedBytes, CHUNK * 3);
  assert.equal(q.fatalError, null);
  assert.equal(q.paused, false);
});
