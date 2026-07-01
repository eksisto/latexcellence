/**
 * 前端入口文件
 *
 * React 应用启动点：
 *   1. 挂载 #root 根节点
 *   2. 导入 Tailwind CSS 样式
 *   3. 渲染 App 组件（内含路由）
 *
 * StrictMode 会在开发模式下触发额外的渲染和副作用检查，
 * 帮助发现潜在问题。
 */
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
