import { test } from "node:test";
import assert from "node:assert/strict";

import { pathBasename, resolveWorkspaceName } from "./projectName.js";

test("pathBasename 兼容 POSIX 路径、末尾分隔符与 Windows 路径", () => {
  assert.equal(pathBasename("/home/user/paper"), "paper");
  assert.equal(pathBasename("/home/user/paper/"), "paper");
  assert.equal(pathBasename("C:\\Users\\Linus\\paper"), "paper");
  assert.equal(pathBasename("C:\\Users\\Linus\\paper\\"), "paper");
  assert.equal(pathBasename("main.tex"), "main.tex");
});

test("pathBasename 对空值与非字符串返回空串", () => {
  assert.equal(pathBasename(null), "");
  assert.equal(pathBasename(undefined), "");
  assert.equal(pathBasename(""), "");
  assert.equal(pathBasename("/"), "");
});

test("多用户模式只使用项目名，不回退到路径", () => {
  assert.equal(
    resolveWorkspaceName({
      multiUser: true,
      projectName: "论文草稿",
      projectPath: "/should/not/be/used",
      rootPath: "/server/private/path",
    }),
    "论文草稿",
  );
  assert.equal(
    resolveWorkspaceName({ multiUser: true, projectName: null }),
    "",
  );
});

test("单用户模式优先 projectPath，其次 rootPath，最后项目名", () => {
  assert.equal(
    resolveWorkspaceName({
      multiUser: false,
      projectPath: "/home/user/chosen",
      rootPath: "/real/target",
    }),
    "chosen",
  );
  assert.equal(
    resolveWorkspaceName({
      multiUser: false,
      projectPath: null,
      rootPath: "/real/target",
    }),
    "target",
  );
  assert.equal(
    resolveWorkspaceName({
      multiUser: false,
      projectPath: null,
      rootPath: null,
      projectName: "fallback",
    }),
    "fallback",
  );
});
