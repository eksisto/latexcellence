import { memo } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";
import CodeBlock from "@/components/CodeBlock";

// 模块级常量，减少重复解析
const remarkPlugins = [remarkGfm, remarkMath];
const rehypePlugins = [rehypeKatex];

const components = {
  code({ inline, className, children, ...props }) {
    const isInline = inline ?? !String(children).includes("\n");
    const language = (className || "").replace(/^language-/, "");
    if (isInline) {
      return (
        <code
          className="code-inline rounded bg-muted px-1 py-0.5 text-xs"
          {...props}
        >
          {children}
        </code>
      );
    }
    return (
      <CodeBlock
        code={String(children)}
        language={language || "plaintext"}
        className="p-3"
      />
    );
  },
  a: ({ node, ...props }) => (
    <a
      className="text-primary relative inline-block no-underline after:content-[''] after:absolute after:left-0 after:bottom-0.5 after:h-[0.7px] after:w-full after:bg-primary after:origin-left after:transition-transform after:duration-300 hover:after:scale-x-0"
      {...props}
      target="_blank"
      rel="noopener noreferrer"
    />
  ),
  ul: ({ node, ...props }) => (
    <ul className="list-disc pl-5 space-y-1 my-1" {...props} />
  ),
  ol: ({ node, ...props }) => (
    <ol className="list-decimal pl-5 space-y-1 my-1" {...props} />
  ),
  blockquote: ({ node, ...props }) => (
    <blockquote
      className="border-l-2 border-muted-foreground/40 pl-3 italic text-muted-foreground my-1"
      {...props}
    />
  ),
  h1: ({ node, ...props }) => (
    <h1 className="text-lg font-semibold mt-3 mb-1" {...props} />
  ),
  h2: ({ node, ...props }) => (
    <h2 className="text-base font-semibold mt-3 mb-1" {...props} />
  ),
  h3: ({ node, ...props }) => (
    <h3 className="text-sm font-semibold mt-2 mb-1" {...props} />
  ),
};

function MarkdownRenderer({ children }) {
  return (
    <div className="prose dark:prose-invert max-w-none text-sm leading-relaxed break-words">
      <Markdown
        remarkPlugins={remarkPlugins}
        rehypePlugins={rehypePlugins}
        components={components}
      >
        {children}
      </Markdown>
    </div>
  );
}

// 聊天流式更新时，未变化的消息会带相同 children 重渲染；
// memo 让 Markdown 解析只在内容真正变化时执行。
export default memo(MarkdownRenderer);
