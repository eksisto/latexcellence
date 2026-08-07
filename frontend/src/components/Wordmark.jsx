import { cn } from "@/lib/utils";

/**
 * 品牌字标 —— 本设计的方向性签名元素。
 * 用学术衬线 STIX Two Text 排 "LaTeXcellence"。
 * 尺寸由外部 font-size 决定，可任意缩放复用。
 */
export default function Wordmark({ className, ...props }) {
  return (
    <span
      className={cn("font-display whitespace-nowrap", className)}
      {...props}
    >
      LaTeXcellence
    </span>
  );
}
