/**
 * 分片上传队列纯状态机（v2 P5 / 断点续传修复）。
 *
 * 从 UploadProjectDialog.jsx 抽出的与 React/网络层无关的部分：
 * 队列对象构建、服务端位图 → 缺失任务对位、单分片指数退避重试、
 * 并发 worker 池的暂停/致命错误推进逻辑。网络动作（切片、MD5、PUT）
 * 与完成回调由调用方注入（uploadPart / onProgress / onComplete），
 * 便于脱离组件用 node:test 直接驱动验证。
 */

// 并发分片数（方案 §10.3）
export const UPLOAD_CONCURRENCY = 3;

// 单个分片的失败重试上限（指数退避：800ms × 2^n）
export const MAX_PART_ATTEMPTS = 5;
export const RETRY_BASE_MS = 800;

/** 后端错误码 → 是否可通过重试恢复（其余一律视为致命错误） */
export function isRetryablePartError(err) {
  // 网络层失败（fetch 抛出）没有 code：可重试
  if (!err?.code) return true;
  // 分片内容校验失败：重新切片重算 MD5 后重试通常可自愈
  return err.code === "part_md5_mismatch";
}

export function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// 可中断退避的轮询步长：暂停/致命错误置位后最迟一个步长内被感知，
// 而不是等满整个退避窗口（第 5 次重试前可达 12.8s）才生效
const BACKOFF_POLL_MS = 50;

/** 可中断退避等待：q.paused / q.fatalError 置位后立即让出，由调用方循环顶部判定 */
async function sleepBackoff(ms, q) {
  let waited = 0;
  while (waited < ms && !q.paused && !q.fatalError) {
    await sleep(Math.min(BACKOFF_POLL_MS, ms - waited));
    waited += BACKOFF_POLL_MS;
  }
}

/**
 * 构建队列运行时对象。files 为 [{ file, size }]；paused/fatalError/
 * running/inflight 由队列驱动器维护，不参与 React 渲染。
 */
export function createUploadQueue({
  sessionId,
  kind,
  name,
  totalBytes,
  chunkPartBytes,
  files,
}) {
  return {
    sessionId,
    kind,
    name,
    totalBytes,
    chunkPartBytes,
    files,
    uploadedBytes: 0,
    paused: false,
    fatalError: null,
    inflight: new Set(),
    running: false,
  };
}

/**
 * （重新）启动队列前复位运行标志。
 *
 * 断点续传关键语义：必须在复位 paused 的同时清空上一次运行遗留的
 * fatalError。服务端会话与已收分片以 status 位图为唯一权威，
 * 清掉本地致命标记后按位图重试是安全的；否则 worker 的
 * while (!paused && !fatalError) 会跳过全部任务并原样重抛陈旧错误，
 * 「继续上传」退化为零网络活动的报错循环（用户只能中止或刷新）。
 */
export function resetForResume(q) {
  q.running = true;
  q.paused = false;
  q.fatalError = null;
  return q;
}

/** 暂停哨兵错误：uploadPartWithRetry 与 runUploadQueue 以 message === "paused" 识别 */
const PAUSED_MESSAGE = "paused";

function pausedError() {
  return new Error(PAUSED_MESSAGE);
}

export function isPausedError(err) {
  return err instanceof Error && err.message === PAUSED_MESSAGE;
}

/**
 * 服务端权威进度对位（纯函数）：按 status 位图重建已收字节基线
 * 与缺失分片任务列表 [{ fi, ci, bytes, offset }]。
 */
export function planTasksFromStatus(st, fallbackChunkBytes) {
  const chunkSize = st.chunk_size || fallbackChunkBytes;
  let receivedBytes = 0;
  const tasks = [];
  st.files.forEach((f) => {
    f.bitmap.forEach((got, ci) => {
      const bytes = Math.min(chunkSize, f.size - ci * chunkSize);
      if (got) {
        receivedBytes += bytes;
      } else {
        tasks.push({ fi: f.index, ci, bytes, offset: ci * chunkSize });
      }
    });
  });
  return { receivedBytes, tasks };
}

/**
 * 单个分片上传（指数退避重试；暂停经 AbortController 中断在途请求）。
 * uploadPart(q, task, signal) 由调用方注入真实网络动作。
 */
export async function uploadPartWithRetry(
  q,
  task,
  uploadPart,
  { retryBaseMs = RETRY_BASE_MS } = {},
) {
  for (let attempt = 1; attempt <= MAX_PART_ATTEMPTS; attempt++) {
    if (q.fatalError) throw q.fatalError;
    if (q.paused) throw pausedError();
    const controller = new AbortController();
    q.inflight.add(controller);
    try {
      await uploadPart(q, task, controller.signal);
      return;
    } catch (err) {
      if (err?.name === "AbortError" || (q.paused && !q.fatalError)) {
        throw pausedError();
      }
      if (!isRetryablePartError(err) || attempt >= MAX_PART_ATTEMPTS) {
        throw err;
      }
      await sleepBackoff(retryBaseMs * 2 ** (attempt - 1), q);
    } finally {
      q.inflight.delete(controller);
    }
  }
}

/**
 * 队列驱动器：并发消费 tasks，直至全部完成 / 暂停 / 致命错误。
 *
 * - 暂停（含在途请求被 abort）：以 message 为 "paused" 的哨兵错误 reject，
 *   不改写 fatalError；
 * - 其余失败：先记入 q.fatalError 再原样 rethrow（与组件 handleFatal
 *   前的状态记录一致），同批 worker 在下一轮循环检测到后退出；
 * - 全部任务完成后执行 onComplete（如 complete 落库），其失败同样按
 *   致命错误处理——两者都可经 resetForResume 复位后重试。
 *
 * q.running 由调用方（组件 runQueue）负责置位/复位。
 */
export async function runUploadQueue(q, tasks, deps = {}) {
  const {
    uploadPart,
    onProgress,
    onComplete,
    concurrency = UPLOAD_CONCURRENCY,
    retryBaseMs = RETRY_BASE_MS,
  } = deps;
  try {
    q.nextTask = 0;
    const workers = Array.from(
      { length: Math.min(concurrency, Math.max(tasks.length, 1)) },
      async () => {
        while (!q.paused && !q.fatalError) {
          const idx = q.nextTask++;
          if (idx >= tasks.length) return;
          const task = tasks[idx];
          await uploadPartWithRetry(q, task, uploadPart, { retryBaseMs });
          q.uploadedBytes += task.bytes;
          onProgress?.();
        }
      },
    );
    await Promise.all(workers);
    if (q.fatalError) throw q.fatalError;
    if (q.paused) throw pausedError();
    await onComplete?.();
  } catch (err) {
    if (isPausedError(err)) throw err;
    q.fatalError = err;
    throw err;
  }
}
