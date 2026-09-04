import test from "node:test";
import assert from "node:assert/strict";

import {
  EDITOR_SESSION_MAX_BYTES,
  EDITOR_SESSION_PAYLOAD_VERSION,
  EDITOR_SESSION_STORAGE_KEY,
  clearSession,
  fitSessionToQuota,
  loadSession,
  loadSessionAdoptingLegacy,
  measureBytes,
  parseSession,
  saveSession,
  serializeSession,
} from "../src/lib/editor/sessionPersistence.js";

function tab(overrides = {}) {
  return {
    filePath: "a.tex",
    buffer: "\\section{A}",
    baseMd5: "m-a",
    dirty: false,
    stale: false,
    lastActive: 1,
    ...overrides,
  };
}

/** 内存版 sessionStorage 桩 */
function memoryStorage() {
  const map = new Map();
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    removeItem: (k) => map.delete(k),
    _map: map,
  };
}

test("measureBytes 按 UTF-8 字节计", () => {
  assert.equal(measureBytes("abc"), 3);
  assert.equal(measureBytes("中"), 3);
  assert.equal(measureBytes(""), 0);
});

test("serializeSession/parseSession 往返保留全部 tab 字段与激活位", () => {
  const tabs = [
    tab({ filePath: "a.tex", dirty: true, buffer: "A-dirty", lastActive: 9 }),
    tab({ filePath: "sub/b.tex", baseMd5: null, lastActive: 5 }),
  ];
  const text = serializeSession({
    projectKey: "proj-1",
    tabs,
    activeTabPath: "a.tex",
  });
  const parsed = parseSession(text);
  assert.equal(parsed.ok, true);
  assert.equal(parsed.projectKey, "proj-1");
  assert.equal(parsed.activeTabPath, "a.tex");
  assert.deepEqual(parsed.tabs, [
    {
      filePath: "a.tex",
      buffer: "A-dirty",
      // v1 形状入参：脏 tab 未携带保存基线 → 置 null（色条降级）
      savedBuffer: null,
      openedBuffer: "A-dirty",
      baseMd5: "m-a",
      dirty: true,
      stale: false,
      lastActive: 9,
    },
    {
      filePath: "sub/b.tex",
      buffer: "\\section{A}",
      // 干净 tab：保存/打开基线按定义等值于 buffer
      savedBuffer: "\\section{A}",
      openedBuffer: "\\section{A}",
      baseMd5: null,
      dirty: false,
      stale: false,
      lastActive: 5,
    },
  ]);
});

test("v2 载荷：脏 tab 携带 savedBuffer，干净 tab 不冗余写入", () => {
  const tabs = [
    tab({
      filePath: "a.tex",
      dirty: true,
      buffer: "A-current",
      savedBuffer: "A-saved",
    }),
    tab({ filePath: "b.tex" }),
  ];
  const text = serializeSession({
    projectKey: "p",
    tabs,
    activeTabPath: "a.tex",
  });
  const data = JSON.parse(text);
  assert.equal(data.version, EDITOR_SESSION_PAYLOAD_VERSION);
  assert.equal(data.tabs[0].savedBuffer, "A-saved");
  assert.equal("savedBuffer" in data.tabs[1], false);
  const parsed = parseSession(text);
  assert.equal(parsed.ok, true);
  assert.deepEqual(parsed.tabs[0], {
    filePath: "a.tex",
    buffer: "A-current",
    savedBuffer: "A-saved",
    // 打开基线不持久化：恢复后回落 savedBuffer（绿色色条从零重建）
    openedBuffer: "A-saved",
    baseMd5: "m-a",
    dirty: true,
    stale: false,
    lastActive: 1,
  });
  assert.equal(parsed.tabs[1].savedBuffer, "\\section{A}");
});

test("parseSession 对损坏 / 结构异常载荷整体放弃", () => {
  assert.equal(parseSession("not json").ok, false);
  assert.equal(parseSession("null").ok, false);
  assert.equal(parseSession(JSON.stringify({ version: 1 })).ok, false);
  assert.equal(parseSession(JSON.stringify({ tabs: "x" })).ok, false);
});

test("parseSession 逐条清洗：非法条目丢弃、activeTabPath 回落合法集合", () => {
  const parsed = parseSession(
    JSON.stringify({
      version: 1,
      projectKey: "p",
      activeTabPath: "bad.tex",
      tabs: [
        {
          filePath: "ok.tex",
          buffer: "x",
          baseMd5: "m",
          dirty: "yes",
          lastActive: "n",
        },
        { filePath: "", buffer: "no-path" },
        { buffer: "no-path-2" },
        "garbage",
        { filePath: "ok2.tex" }, // 缺 buffer → 丢弃
      ],
    }),
  );
  assert.equal(parsed.ok, true);
  assert.deepEqual(
    parsed.tabs.map((t) => t.filePath),
    ["ok.tex"],
  );
  // activeTabPath 不在剩余集合 → 回落首个
  assert.equal(parsed.activeTabPath, "ok.tex");
  // 类型非法字段回落默认值
  assert.equal(parsed.tabs[0].dirty, false);
  assert.equal(parsed.tabs[0].lastActive, 0);
});

test("parseSession 空 tabs 时 activeTabPath 为 null", () => {
  const parsed = parseSession(JSON.stringify({ tabs: [], activeTabPath: "x" }));
  assert.equal(parsed.ok, true);
  assert.equal(parsed.activeTabPath, null);
});

test("fitSessionToQuota 配额内原样通过", () => {
  const fit = fitSessionToQuota([tab()], "a.tex", { projectKey: "p" });
  assert.equal(fit.ok, true);
  assert.equal(fit.overflow, false);
  assert.deepEqual(fit.evicted, []);
  assert.equal(fit.tabs.length, 1);
});

test("fitSessionToQuota 超限时按 LRU 淘汰最久不活跃的非脏 tab", () => {
  const tabs = [
    tab({ filePath: "old.tex", lastActive: 1 }),
    tab({ filePath: "mid.tex", lastActive: 5 }),
    tab({ filePath: "keep.tex", lastActive: 9 }),
    tab({ filePath: "new.tex", lastActive: 9, dirty: true }),
  ];
  // 限制 = 仅保留 keep + new 时的载荷体积 → 恰好淘汰 old / mid 后达标；
  // 脏 tab（new.tex）即使更久未活跃也不得淘汰
  const fit = fitSessionToQuota(tabs, "new.tex", {
    projectKey: "p",
    limitBytes: measureBytes(
      serializeSession({
        projectKey: "p",
        tabs: [tabs[2], tabs[3]],
        activeTabPath: "new.tex",
      }),
    ),
  });
  assert.equal(fit.ok, true);
  assert.equal(fit.overflow, false);
  assert.deepEqual(fit.evicted, ["old.tex", "mid.tex"]);
  assert.deepEqual(
    fit.tabs.map((t) => t.filePath),
    ["keep.tex", "new.tex"],
  );
  assert.equal(fit.activeTabPath, "new.tex");
});

test("fitSessionToQuota 全为脏 tab 仍超限 → overflow 且不淘汰", () => {
  const tabs = [
    tab({ filePath: "d1.tex", dirty: true, buffer: "x".repeat(100) }),
    tab({ filePath: "d2.tex", dirty: true, buffer: "y".repeat(100) }),
  ];
  const fit = fitSessionToQuota(tabs, "d1.tex", {
    projectKey: "p",
    limitBytes: 10,
  });
  assert.equal(fit.ok, false);
  assert.equal(fit.overflow, true);
  assert.deepEqual(fit.evicted, []);
  assert.equal(fit.tabs.length, 2);
});

test("fitSessionToQuota 淘汰激活 tab 时激活位右移接管、末尾回落左", () => {
  const tabs = [
    tab({ filePath: "a.tex", lastActive: 1 }),
    tab({ filePath: "b.tex", lastActive: 2 }),
  ];
  const fit = fitSessionToQuota(tabs, "a.tex", {
    projectKey: "p",
    limitBytes: 10,
  });
  // a、b 均非脏：淘汰 a（最久）后激活位由 b 接管；b 也被淘汰后置 null
  assert.equal(fit.ok, false);
  assert.equal(fit.overflow, true);
  assert.deepEqual(fit.evicted, ["a.tex", "b.tex"]);
  assert.equal(fit.tabs.length, 0);
  assert.equal(fit.activeTabPath, null);
});

test("saveSession 写入 sessionStorage；overflow 不写", () => {
  const storage = memoryStorage();
  const ok = saveSession(
    storage,
    { projectKey: "p", tabs: [tab()], activeTabPath: "a.tex" },
    { limitBytes: EDITOR_SESSION_MAX_BYTES },
  );
  assert.equal(ok.ok, true);
  assert.ok(storage.getItem(EDITOR_SESSION_STORAGE_KEY));

  const refused = saveSession(
    memoryStorage(),
    { projectKey: "p", tabs: [tab({ dirty: true })], activeTabPath: "a.tex" },
    { limitBytes: 10 },
  );
  assert.equal(refused.ok, false);
  assert.equal(refused.overflow, true);
});

test("loadSession 读取 + 项目键隔离：不匹配 / 缺失 / 损坏均放弃", () => {
  const storage = memoryStorage();
  assert.equal(loadSession(storage, "p").reason, "missing");
  storage.setItem(EDITOR_SESSION_STORAGE_KEY, "{broken");
  assert.equal(loadSession(storage, "p").reason, "corrupt");
  saveSession(storage, {
    projectKey: "p",
    tabs: [tab()],
    activeTabPath: "a.tex",
  });
  assert.equal(loadSession(storage, "other").reason, "project_mismatch");
  const ok = loadSession(storage, "p");
  assert.equal(ok.ok, true);
  assert.equal(ok.tabs.length, 1);
  assert.equal(ok.activeTabPath, "a.tex");
});

test("clearSession 移除载荷", () => {
  const storage = memoryStorage();
  saveSession(storage, {
    projectKey: "p",
    tabs: [tab()],
    activeTabPath: "a.tex",
  });
  clearSession(storage);
  assert.equal(storage.getItem(EDITOR_SESSION_STORAGE_KEY), null);
});

test("loadSessionAdoptingLegacy 主存缺失时收养 sessionStorage 遗留并清出", () => {
  const local = memoryStorage();
  const legacy = memoryStorage();
  saveSession(legacy, {
    projectKey: "p",
    tabs: [tab()],
    activeTabPath: "a.tex",
  });
  const adopted = loadSessionAdoptingLegacy(local, legacy, "p");
  assert.equal(adopted.ok, true);
  assert.equal(adopted.adoptedLegacy, true);
  assert.equal(adopted.tabs.length, 1);
  // 收养后遗留载荷清出（后续 saveSession 自然落到主存）
  assert.equal(legacy.getItem(EDITOR_SESSION_STORAGE_KEY), null);
  // 主存已有载荷时不理会遗留
  saveSession(local, {
    projectKey: "p",
    tabs: [tab({ filePath: "local.tex" })],
    activeTabPath: "local.tex",
  });
  saveSession(legacy, {
    projectKey: "p",
    tabs: [tab({ filePath: "legacy.tex" })],
    activeTabPath: "legacy.tex",
  });
  const primary = loadSessionAdoptingLegacy(local, legacy, "p");
  assert.equal(primary.ok, true);
  assert.equal(primary.adoptedLegacy, undefined);
  assert.equal(primary.tabs[0].filePath, "local.tex");
  // 双双缺失 → missing；遗留损坏 / 项目不匹配不迁移
  assert.equal(
    loadSessionAdoptingLegacy(memoryStorage(), memoryStorage(), "p").reason,
    "missing",
  );
  const corrupt = memoryStorage();
  corrupt.setItem(EDITOR_SESSION_STORAGE_KEY, "{broken");
  assert.equal(
    loadSessionAdoptingLegacy(memoryStorage(), corrupt, "p").reason,
    "missing",
  );
  const mismatch = memoryStorage();
  saveSession(mismatch, {
    projectKey: "other",
    tabs: [tab()],
    activeTabPath: "a.tex",
  });
  assert.equal(
    loadSessionAdoptingLegacy(memoryStorage(), mismatch, "p").reason,
    "missing",
  );
});

test("saveSession 对注入 storage 抛错时静默放弃不落盘", () => {
  const throwing = {
    getItem: () => null,
    setItem: () => {
      throw new Error("quota");
    },
    removeItem: () => {},
  };
  const result = saveSession(throwing, {
    projectKey: "p",
    tabs: [tab()],
    activeTabPath: "a.tex",
  });
  assert.equal(result.ok, false);
  assert.equal(result.overflow, false);
});
