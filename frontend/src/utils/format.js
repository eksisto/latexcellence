/**
 * 展示格式化共享工具。
 *
 * 此前 formatBytes（项目工作台）与 humanSize（上传对话框）、formatDate
 * （工作台 / 管理面板）各自维护着逐行等价的实现（审查 L9），统一收敛到本模块。
 */

/** 字节数人性化展示（B/KB/MB/GB，1024 进制）；非有限数或 ≤0 返回 "0 B"。 */
export function formatBytes(bytes) {
  const n = Number(bytes) || 0;
  if (n <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.min(
    Math.floor(Math.log(n) / Math.log(1024)),
    units.length - 1,
  );
  return `${(n / 1024 ** i).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

/** ISO 时间 → 本地化字符串；空值或无法解析返回 "—"。 */
export function formatDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString();
}
