import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";

const components = {
  code({ inline, className, children, ...props }) {
    if (inline) {
      return (
        <code
          className="rounded bg-muted px-1 py-0.5 font-mono text-xs"
          {...props}
        >
          {children}
        </code>
      );
    }
    return (
      <pre className="rounded-md bg-muted p-3 overflow-x-auto text-xs font-mono leading-relaxed">
        <code className={className} {...props}>
          {children}
        </code>
      </pre>
    );
  },
  a: ({ node, ...props }) => (
    <a {...props} target="_blank" rel="noopener noreferrer" />
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

export default function MarkdownRenderer({ children }) {
  return (
    <div className="prose dark:prose-invert max-w-none text-sm leading-relaxed break-words">
      <Markdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={components}
      >
        {children}
      </Markdown>
    </div>
  );
}