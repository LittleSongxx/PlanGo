import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

// User-facing model answers (chat and the outcome canvas). Source quotes stay plain text.
export function Markdown({ children }: { children: string }): JSX.Element {
  return (
    <div className="md-body text-[13px] leading-[1.75] break-words">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children }) => <div className="font-semibold text-[15px] mt-2 mb-1 first:mt-0">{children}</div>,
          h2: ({ children }) => <div className="font-semibold text-sm mt-2 mb-1 first:mt-0">{children}</div>,
          h3: ({ children }) => <div className="font-semibold text-sm mt-1.5 mb-0.5">{children}</div>,
          p: ({ children }) => <p className="my-1 first:mt-0 last:mb-0">{children}</p>,
          ul: ({ children }) => <ul className="my-1 pl-4 list-disc space-y-0.5 marker:text-neutral-400">{children}</ul>,
          ol: ({ children }) => <ol className="my-1 pl-4 list-decimal space-y-0.5 marker:text-neutral-400">{children}</ol>,
          li: ({ children }) => <li className="leading-relaxed">{children}</li>,
          strong: ({ children }) => <strong className="font-semibold text-brand-ink">{children}</strong>,
          hr: () => <hr className="my-2 border-neutral-200" />,
          blockquote: ({ children }) => <blockquote className="my-1.5 pl-2.5 border-l-2 border-brand/50 text-neutral-500 text-[13px]">{children}</blockquote>,
          a: ({ children, href }) => (
            <a href={href} target="_blank" rel="noopener noreferrer" className="text-brand-ink underline underline-offset-2">
              {children}
            </a>
          ),
          code: ({ children }) => <code className="px-1 py-0.5 rounded bg-neutral-200/70 text-[12px]">{children}</code>,
          table: ({ children }) => (
            <div className="my-1.5 overflow-x-auto rounded-lg border border-neutral-200">
              <table className="w-full text-[12px] border-collapse">{children}</table>
            </div>
          ),
          thead: ({ children }) => <thead className="bg-neutral-100">{children}</thead>,
          th: ({ children }) => <th className="px-2 py-1 text-left font-semibold border-b border-neutral-200 whitespace-nowrap">{children}</th>,
          td: ({ children }) => <td className="px-2 py-1 border-b border-neutral-100 align-top">{children}</td>
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  )
}
