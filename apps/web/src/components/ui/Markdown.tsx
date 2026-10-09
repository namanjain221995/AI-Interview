import { useMemo, type ComponentPropsWithoutRef } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import { cn } from "@/lib/utils";

import { CodeBlock } from "./CodeBlock";

function isExternal(href: string | undefined): boolean {
  return !!href && /^https?:\/\//i.test(href) && !href.startsWith(window.location.origin);
}

const components: Components = {
  h1: ({ node: _node, className, ...props }) => <h2 className={cn("mt-6 mb-2 text-lg font-semibold tracking-[-0.01em] text-fg first:mt-0", className)} {...props} />,
  h2: ({ node: _node, className, ...props }) => <h3 className={cn("mt-6 mb-2 text-base font-semibold text-fg first:mt-0", className)} {...props} />,
  h3: ({ node: _node, className, ...props }) => <h4 className={cn("mt-5 mb-1.5 text-[15px] font-semibold text-fg first:mt-0", className)} {...props} />,
  h4: ({ node: _node, className, ...props }) => <h5 className={cn("mt-4 mb-1 text-sm font-semibold text-fg first:mt-0", className)} {...props} />,
  p: ({ node: _node, className, ...props }) => <p className={cn("my-3 first:mt-0 last:mb-0", className)} {...props} />,
  ul: ({ node: _node, className, ...props }) => <ul className={cn("my-3 list-disc space-y-1.5 pl-5 marker:text-fg-subtle", className)} {...props} />,
  ol: ({ node: _node, className, ...props }) => <ol className={cn("my-3 list-decimal space-y-1.5 pl-5 marker:text-fg-subtle", className)} {...props} />,
  li: ({ node: _node, className, ...props }) => <li className={cn("pl-1", className)} {...props} />,
  a: ({ node: _node, className, href, ...props }) => (
    <a
      href={href}
      className={cn("font-medium text-accent underline decoration-accent/30 underline-offset-2 hover:decoration-accent", className)}
      {...(isExternal(href) ? { target: "_blank", rel: "noopener noreferrer" } : {})}
      {...props}
    />
  ),
  blockquote: ({ node: _node, className, ...props }) => (
    <blockquote className={cn("my-4 border-l-2 border-line-strong pl-4 text-fg-muted", className)} {...props} />
  ),
  hr: ({ node: _node, ...props }) => <hr className="my-6 border-line" {...props} />,
  table: ({ node: _node, className, ...props }) => (
    <div className="my-4 overflow-x-auto rounded-md border border-line scrollbar-thin contain-inline">
      <table className={cn("w-full border-collapse text-sm", className)} {...props} />
    </div>
  ),
  th: ({ node: _node, className, ...props }) => (
    <th className={cn("border-b border-line bg-surface-subtle px-3 py-2 text-left text-xs font-medium text-fg-muted", className)} {...props} />
  ),
  td: ({ node: _node, className, ...props }) => <td className={cn("border-b border-line-subtle px-3 py-2 align-top", className)} {...props} />,
  pre: ({ children }) => <>{children}</>,
};

function codeRenderer(options: { wrap: boolean; copyable: boolean }): Components["code"] {
  return function MarkdownCode({ node: _node, className, children, ...props }) {
    const match = /language-([\w+-]+)/.exec(className ?? "");
    const text = String(children ?? "");
    const isBlock = Boolean(match) || text.includes("\n");
    if (isBlock) {
      return <CodeBlock code={text.replace(/\n$/, "")} language={match?.[1] ?? null} wrap={options.wrap} copyable={options.copyable} className="my-4" />;
    }
    return (
      <code className="rounded-xs border border-line-subtle bg-surface-subtle px-1 py-px font-mono text-[0.85em] text-fg" {...(props as ComponentPropsWithoutRef<"code">)}>
        {children}
      </code>
    );
  };
}

export interface MarkdownProps {
  children: string;
  size?: "sm" | "md";
  /** Wrap long lines in code blocks instead of scrolling them (narrow panels). */
  wrapCode?: boolean;
  /** Show a copy button on code blocks (default true). */
  copyableCode?: boolean;
  className?: string;
}

/**
 * Renders untrusted Markdown safely: GitHub-flavoured syntax, no raw HTML
 * (skipped), and react-markdown's URL sanitiser blocks javascript: links.
 */
export function Markdown({ children, size = "md", wrapCode = false, copyableCode = true, className }: MarkdownProps) {
  const rendered = useMemo<Components>(() => ({ ...components, code: codeRenderer({ wrap: wrapCode, copyable: copyableCode }) }), [wrapCode, copyableCode]);
  return (
    <div className={cn("min-w-0 text-fg [overflow-wrap:anywhere]", size === "md" ? "text-sm leading-6" : "text-[13px] leading-5", className)}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={rendered}>
        {children}
      </ReactMarkdown>
    </div>
  );
}
