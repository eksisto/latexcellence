import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";
import { i18nReady } from "./i18n";

import.meta.glob("./theme/*.css", { eager: true });

// 等待 i18n 初始化完成（含非默认语言的动态语言包加载）再渲染，
// 避免非 zh 用户首帧闪现简体中文 fallback 文案
i18nReady.then(() => {
  ReactDOM.createRoot(document.getElementById("root")).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
});
