import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import {
  getBaseName,
  joinProjectPath,
  validateNewDirName,
} from "../src/lib/editor/files.js";
import { remapMovedPath, remapMovedPaths } from "../src/lib/editor/tabs.js";

/* ── remapMovedPath：单路径重映射核心（editor-file-move-design §5.2 / Q6） ── */

test("remapMovedPath：文件精确命中，未命中原样返回", () => {
  assert.equal(
    remapMovedPath("chapters/a.tex", "chapters/a.tex", "intro.tex"),
    "intro.tex",
  );
  assert.equal(remapMovedPath("a.tex", "a.tex", "figs/a.tex"), "figs/a.tex");
  // 未命中 / 非法输入原样返回
  assert.equal(remapMovedPath("other.tex", "a.tex", "b.tex"), "other.tex");
  assert.equal(remapMovedPath(null, "a.tex", "b.tex"), null);
  assert.equal(remapMovedPath("a.tex", "", "b.tex"), "a.tex");
  assert.equal(remapMovedPath("a.tex", "a.tex", ""), "a.tex");
});

test("remapMovedPath：目录前缀一次匹配覆盖整棵子树", () => {
  // 目录自身精确命中
  assert.equal(remapMovedPath("figs", "figs", "figures", true), "figures");
  // 子树内各级后代随迁
  assert.equal(
    remapMovedPath("figs/a.tex", "figs", "figures", true),
    "figures/a.tex",
  );
  assert.equal(
    remapMovedPath("figs/nested/deep.bib", "figs", "figures", true),
    "figures/nested/deep.bib",
  );
  // 无命中原样返回
  assert.equal(
    remapMovedPath("other.tex", "figs", "figures", true),
    "other.tex",
  );
});

test("remapMovedPath：前缀带 / 边界，figs 不误伤 figs2", () => {
  assert.equal(
    remapMovedPath("figs2/a.tex", "figs", "figures", true),
    "figs2/a.tex",
  );
  assert.equal(
    remapMovedPath("figs-backup", "figs", "figures", true),
    "figs-backup",
  );
});

/* ── remapMovedPaths：会话重映射（tabs + activeTabPath） ────────────────── */

function makeTab(filePath, overrides = {}) {
  return {
    filePath,
    buffer: `content of ${filePath}`,
    savedBuffer: `content of ${filePath}`,
    openedBuffer: `content of ${filePath}`,
    baseMd5: `md5-${filePath}`,
    dirty: false,
    stale: false,
    lastActive: 1,
    ...overrides,
  };
}

test("remapMovedPaths：精确命中文件，buffer/baseMd5/脏标记原样保留（Q6）", () => {
  const dirtyTab = makeTab("notes.tex", { dirty: true, baseMd5: "base-1" });
  const session = {
    tabs: [makeTab("main.tex"), dirtyTab, makeTab("refs.bib")],
    activeTabPath: "main.tex",
  };
  const next = remapMovedPaths(session, "notes.tex", "chapters/notes.tex");
  assert.equal(next.tabs.length, 3);
  const moved = next.tabs.find((t) => t.filePath === "chapters/notes.tex");
  assert.ok(moved);
  // buffer / savedBuffer / baseMd5 / 脏标记 / 陈旧标记原样保留
  assert.equal(moved.buffer, dirtyTab.buffer);
  assert.equal(moved.savedBuffer, dirtyTab.savedBuffer);
  assert.equal(moved.baseMd5, "base-1");
  assert.equal(moved.dirty, true);
  assert.equal(moved.lastActive, dirtyTab.lastActive);
  // 旧路径不再存在
  assert.ok(!next.tabs.some((t) => t.filePath === "notes.tex"));
  // 激活位不受影响
  assert.equal(next.activeTabPath, "main.tex");
});

test("remapMovedPaths：目录前缀命中整棵子树，无关 tab 原引用不动", () => {
  const untouchedA = makeTab("main.tex");
  const movedChild = makeTab("figs/nested/deep.tex", { dirty: true });
  const movedDir = makeTab("figs/data.bib");
  const untouchedB = makeTab("figs2/lookalike.tex");
  const session = {
    tabs: [untouchedA, movedChild, movedDir, untouchedB],
    activeTabPath: "figs/data.bib",
  };
  const next = remapMovedPaths(session, "figs", "figures", { isDir: true });
  // 无关 tab 保持同一对象引用（map 未命中不复制）
  assert.equal(next.tabs[0], untouchedA);
  assert.equal(next.tabs[3], untouchedB);
  // 子树整体随迁
  assert.equal(next.tabs[1].filePath, "figures/nested/deep.tex");
  assert.equal(next.tabs[2].filePath, "figures/data.bib");
  // 子树内的激活位同步重映射
  assert.equal(next.activeTabPath, "figures/data.bib");
  // 脏标记随迁保留
  assert.equal(next.tabs[1].dirty, true);
  assert.ok(!next.tabs.some((t) => t.filePath.startsWith("figs/")));
});

test("remapMovedPaths：激活位为被移动文件时重映射 activeTabPath", () => {
  const session = {
    tabs: [makeTab("main.tex"), makeTab("a.tex")],
    activeTabPath: "a.tex",
  };
  const next = remapMovedPaths(session, "a.tex", "chapters/a.tex");
  assert.equal(next.activeTabPath, "chapters/a.tex");
});

test("只读预览归属路径与 tab 同口径重映射（移动后预览跟随，不悬空旧路径）", () => {
  // 预览中的被移动文件：精确命中换路（EditorPage 预览 effect 按新路径重拉）
  assert.equal(
    remapMovedPath(
      "figs/diagram.pdf",
      "figs/diagram.pdf",
      "backup/diagram.pdf",
    ),
    "backup/diagram.pdf",
  );
  // 预览中的子树文件（目录整体移动）：前缀命中换路
  assert.equal(
    remapMovedPath("figs/nested/pic.png", "figs", "figures", true),
    "figures/nested/pic.png",
  );
  // 无关预览与无预览（null）原样保留，不触发预览 effect 重拉
  assert.equal(
    remapMovedPath("other.pdf", "figs", "figures", true),
    "other.pdf",
  );
  assert.equal(remapMovedPath(null, "figs", "figures", true), null);
});

test("remapMovedPaths：无任何命中时原样返回 session 引用（空转）", () => {
  const session = {
    tabs: [makeTab("main.tex"), makeTab("refs.bib")],
    activeTabPath: "main.tex",
  };
  assert.equal(remapMovedPaths(session, "ghost.tex", "moved.tex"), session);
  assert.equal(
    remapMovedPaths(session, "figs", "figures", { isDir: true }),
    session,
  );
});

/* ── joinProjectPath + getBaseName：落位路径拼接回归（§5.2） ───────────── */

test("getBaseName：取路径末段，无分隔符时原样返回", () => {
  assert.equal(getBaseName("chapters/intro.tex"), "intro.tex");
  assert.equal(getBaseName("main.tex"), "main.tex");
  assert.equal(getBaseName("figs/nested/deep.bib"), "deep.bib");
  assert.equal(getBaseName(""), "");
  assert.equal(getBaseName(null), "");
});

test("拖拽落位路径拼接：to_path = joinProjectPath(targetDir, basename)（§5.2）", () => {
  // 文件拖入子目录
  assert.equal(
    joinProjectPath("chapters", getBaseName("intro.tex")),
    "chapters/intro.tex",
  );
  // 拖到项目根（targetDir = null）
  assert.equal(joinProjectPath(null, getBaseName("figs/a.tex")), "a.tex");
  // 目录拖入子目录（整棵随迁）
  assert.equal(joinProjectPath("backup", getBaseName("figs")), "backup/figs");
  // 拖回原目录 = 原路径（handleMoveEntry 据此静默忽略不发请求）
  assert.equal(
    joinProjectPath("figs", getBaseName("figs/a.tex")),
    "figs/a.tex",
  );
});

/* ── validateNewDirName：与服务端 create-dir 名字消毒同口径（§5.3） ─────── */

test("validateNewDirName：合法目录名放行（不限扩展名）", () => {
  assert.equal(validateNewDirName("chapters"), null);
  assert.equal(validateNewDirName("  figs-v2  "), null);
  assert.equal(validateNewDirName("v1.0"), null);
  assert.equal(validateNewDirName("figs"), null);
});

test("validateNewDirName：空名/分隔符/控制字符/点开头返回错误码", () => {
  assert.equal(validateNewDirName(""), "empty");
  assert.equal(validateNewDirName("   "), "empty");
  assert.equal(validateNewDirName(null), "empty");
  assert.equal(validateNewDirName("a/b"), "separator");
  assert.equal(validateNewDirName("a\\b"), "separator");
  assert.equal(validateNewDirName("a\u0000b"), "control");
  assert.equal(validateNewDirName("a\nb"), "control");
  assert.equal(validateNewDirName(".git"), "hidden");
  assert.equal(validateNewDirName(".."), "hidden");
});

/* ── i18n 消费契约钉死（动态键门禁缺口回归，参照 uploadFileError 先例） ── */

test("移动/新建目录错误码与三语条目一一对应", () => {
  const localesDir = join(
    dirname(fileURLToPath(import.meta.url)),
    "../src/i18n/locales",
  );
  for (const locale of ["zh", "zh-TW", "en"]) {
    const dict = JSON.parse(
      readFileSync(join(localesDir, `${locale}.json`), "utf8"),
    );
    // errors 命名空间：move / create-dir 的新错误码
    for (const key of ["targetExists", "intoSelf"]) {
      assert.ok(
        typeof dict.errors?.[key] === "string" && dict.errors[key].length > 0,
        `${locale}.errors.${key} 缺失`,
      );
    }
    // dirNameError 的错误码经模板字符串动态消费（i18nCoverage 不可见）
    for (const code of ["empty", "separator", "control", "hidden"]) {
      assert.ok(
        typeof dict.components?.projectExplorer?.dirNameError?.[code] ===
          "string" &&
          dict.components.projectExplorer.dirNameError[code].length > 0,
        `${locale}.components.projectExplorer.dirNameError.${code} 缺失`,
      );
    }
    // pages.editor 与 projectExplorer 的新增静态键（防三语同缺）
    const editorKeys = [
      "moved",
      "moveFailed",
      "folderCreated",
      "createFolderFailed",
      "folderDeleted",
      "deleteFolderTitle",
      "deleteFolderConfirmDescription",
      "deleteFolderConfirmProceed",
    ];
    for (const key of editorKeys) {
      assert.ok(
        typeof dict.pages?.editor?.[key] === "string" &&
          dict.pages.editor[key].length > 0,
        `${locale}.pages.editor.${key} 缺失`,
      );
    }
    const explorerKeys = [
      "createFolder",
      "createFolderHere",
      "deleteFolder",
      "createFolderTitle",
      "createFolderInRoot",
      "createFolderInDir",
      "dirNamePlaceholder",
      "dirNameHint",
    ];
    for (const key of explorerKeys) {
      assert.ok(
        typeof dict.components?.projectExplorer?.[key] === "string" &&
          dict.components.projectExplorer[key].length > 0,
        `${locale}.components.projectExplorer.${key} 缺失`,
      );
    }
  }
  // 后端错误码 → errors 命名空间映射链路：EXACT_MAP 登记是 translateError
  // 命中的前提（multiUserLocales 的固定 ERROR_CODES 清单不含本阶段错误码，
  // 此处钉死该映射不被静默删改）
  const errorMessagesSrc = readFileSync(
    join(
      dirname(fileURLToPath(import.meta.url)),
      "../src/utils/errorMessages.js",
    ),
    "utf8",
  );
  assert.match(errorMessagesSrc, /target_exists:\s*"targetExists"/);
  assert.match(errorMessagesSrc, /into_self:\s*"intoSelf"/);
});
