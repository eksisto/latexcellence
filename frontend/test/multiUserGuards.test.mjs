import test from "node:test";
import assert from "node:assert/strict";

import {
  isProjectOpened,
  hasWriteRole,
  emptyStateCta,
} from "../src/utils/projectAccess.js";

// 回归：多用户模式下 projectPath 恒为 null，空态守卫若判 projectPath
// 会导致模块页整体不可达（审查 C1），必须按 projectId 判定
test("isProjectOpened 单用户按 projectPath 判定", () => {
  assert.equal(
    isProjectOpened({
      multiUser: false,
      projectPath: "/tmp/demo",
      projectId: null,
    }),
    true,
  );
  assert.equal(
    isProjectOpened({ multiUser: false, projectPath: null, projectId: "p1" }),
    false,
  );
});

test("isProjectOpened 多用户按 projectId 判定（projectPath 恒 null）", () => {
  assert.equal(
    isProjectOpened({ multiUser: true, projectPath: null, projectId: "abc" }),
    true,
  );
  // 核心回归点：多用户下残留的 projectPath 不应放行守卫
  assert.equal(
    isProjectOpened({
      multiUser: true,
      projectPath: "/legacy",
      projectId: null,
    }),
    false,
  );
  assert.equal(
    isProjectOpened({ multiUser: true, projectPath: null, projectId: "" }),
    false,
  );
});

// 回归：写权限必须白名单（owner/editor），角色未知（null）时不得静默放行（审查 R3）
test("hasWriteRole 白名单：仅 owner / editor 可写", () => {
  assert.equal(hasWriteRole("owner"), true);
  assert.equal(hasWriteRole("editor"), true);
  assert.equal(hasWriteRole("viewer"), false);
  // 角色缺失（项目列表拉取失败等）一律只读
  assert.equal(hasWriteRole(null), false);
  assert.equal(hasWriteRole(undefined), false);
  assert.equal(hasWriteRole(""), false);
});

test("emptyStateCta 按模式返回去向与文案 key", () => {
  assert.deepEqual(emptyStateCta(true), {
    to: "/projects",
    labelKey: "openProjects",
  });
  assert.deepEqual(emptyStateCta(false), {
    to: "/workspace",
    labelKey: "openWorkspace",
  });
});
