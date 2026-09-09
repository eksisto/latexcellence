import test from "node:test";
import assert from "node:assert/strict";

import {
  getFileTreeMenuItems,
  resolveNodeMenuTarget,
} from "../src/lib/editor/contextMenuItems.js";
import {
  getDirName,
  getBaseName,
  isRenameNoop,
  joinProjectPath,
  validateNewDirName,
} from "../src/lib/editor/files.js";

/* ── resolveNodeMenuTarget：树节点 → 菜单目标类型（决策 Q13） ────────── */

test("resolveNodeMenuTarget：目录归为 dir，文件按 .tex / 其他分流", () => {
  assert.deepEqual(
    resolveNodeMenuTarget(
      { is_dir: true, name: "chapters", path: "chapters" },
      null,
    ),
    { targetType: "dir", isMainFile: false },
  );
  assert.deepEqual(
    resolveNodeMenuTarget(
      { is_dir: false, name: "main.tex", path: "main.tex" },
      null,
    ),
    { targetType: "tex_file", isMainFile: false },
  );
  assert.deepEqual(
    resolveNodeMenuTarget(
      { is_dir: false, name: "fig.png", path: "fig.png" },
      null,
    ),
    { targetType: "other_file", isMainFile: false },
  );
  // 后缀大小写不敏感（与 isEditableFilePath 口径一致）
  assert.equal(
    resolveNodeMenuTarget(
      { is_dir: false, name: "Main.TEX", path: "Main.TEX" },
      null,
    ).targetType,
    "tex_file",
  );
});

test("resolveNodeMenuTarget：主文件命中标记 isMainFile 且必为 .tex 目标", () => {
  assert.deepEqual(
    resolveNodeMenuTarget(
      { is_dir: false, name: "main.tex", path: "nested/main.tex" },
      "nested/main.tex",
    ),
    { targetType: "tex_file", isMainFile: true },
  );
  // 主文件记录不命中时不标记
  assert.equal(
    resolveNodeMenuTarget(
      { is_dir: false, name: "a.tex", path: "a.tex" },
      "main.tex",
    ).isMainFile,
    false,
  );
});

/* ── getFileTreeMenuItems：§3.1 菜单项矩阵 ──────────────────────────── */

test("菜单矩阵：.tex 非主文件 = 设为主文件 ┃ 重命名 ┃ 删除", () => {
  const items = getFileTreeMenuItems({ targetType: "tex_file" });
  assert.deepEqual(
    items.map((i) => i.id),
    ["setMainFile", "rename", "delete"],
  );
  // 组间分隔线语义：设为主文件独占 group 1
  assert.deepEqual([...new Set(items.map((i) => i.group))], [1, 2]);
});

test("菜单矩阵：当前主文件不出现「设为主文件」（决策 Q3）", () => {
  const items = getFileTreeMenuItems({
    targetType: "tex_file",
    isMainFile: true,
  });
  assert.deepEqual(
    items.map((i) => i.id),
    ["rename", "delete"],
  );
});

test("菜单矩阵：其他文件 = 重命名 ┃ 删除", () => {
  assert.deepEqual(
    getFileTreeMenuItems({ targetType: "other_file" }).map((i) => i.id),
    ["rename", "delete"],
  );
});

test("菜单矩阵：目录 = 新建文件 ┃ 新建文件夹 ┃ 上传到此处 ┃ 重命名 ┃ 删除", () => {
  const items = getFileTreeMenuItems({ targetType: "dir" });
  assert.deepEqual(
    items.map((i) => i.id),
    ["newFile", "newFolder", "uploadHere", "rename", "delete"],
  );
  // 上传项上传进行中禁用（§3.2-5），且仅上传项声明 uploadingSensitive
  assert.equal(
    items
      .filter((i) => i.uploadingSensitive)
      .map((i) => i.id)
      .join(","),
    "uploadHere",
  );
});

test("菜单矩阵：根 / 背景 = 新建文件 ┃ 新建文件夹 ┃ 上传", () => {
  const items = getFileTreeMenuItems({ targetType: "root" });
  assert.deepEqual(
    items.map((i) => i.id),
    ["newFile", "newFolder", "upload"],
  );
  assert.equal(
    items
      .filter((i) => i.uploadingSensitive)
      .map((i) => i.id)
      .join(","),
    "upload",
  );
});

test("菜单矩阵：viewer（canWrite=false）返回空清单（决策 Q6）", () => {
  for (const targetType of ["dir", "tex_file", "other_file", "root"]) {
    assert.deepEqual(
      getFileTreeMenuItems({ targetType, isMainFile: true, canWrite: false }),
      [],
    );
  }
});

/* ── 重命名纯函数（决策 Q7 / Q10d / Q11） ───────────────────────────── */

test("getDirName：取目录部分，根目录返回 null（与 joinProjectPath 根语义对齐）", () => {
  assert.equal(getDirName("chapters/a.tex"), "chapters");
  assert.equal(getDirName("a/b/c.tex"), "a/b");
  assert.equal(getDirName("main.tex"), null);
  assert.equal(getDirName(""), null);
});

test("isRenameNoop：同名（含空白差异）为 no-op，改名 / 改后缀不是", () => {
  assert.equal(isRenameNoop("chapters/a.tex", "a.tex"), true);
  assert.equal(isRenameNoop("chapters/a.tex", "  a.tex  "), true);
  assert.equal(isRenameNoop("main.tex", "main.tex"), true);
  assert.equal(isRenameNoop("chapters/a.tex", "b.tex"), false);
  // 改后缀允许（决策 Q10d）：不设白名单闸
  assert.equal(isRenameNoop("main.tex", "main.txt"), false);
});

test("重命名落位拼接：toPath = joinProjectPath(getDirName(from), newName)", () => {
  assert.equal(
    joinProjectPath(getDirName("chapters/a.tex"), "b.tex"),
    "chapters/b.tex",
  );
  assert.equal(joinProjectPath(getDirName("main.tex"), "new.tex"), "new.tex");
});

test("rename 校验口径：消毒同 dir、不限 EDITABLE_SUFFIXES（决策 Q10d）", () => {
  // 图片 / bib / sty 均可改名
  assert.equal(validateNewDirName("fig.png"), null);
  assert.equal(validateNewDirName("refs.bib"), null);
  assert.equal(validateNewDirName("style.sty"), null);
  // 消毒分支仍然生效
  assert.equal(validateNewDirName("a/b"), "separator");
  assert.equal(validateNewDirName(".hidden"), "hidden");
  assert.equal(validateNewDirName(""), "empty");
  assert.equal(validateNewDirName("a\u0000b"), "control");
});
