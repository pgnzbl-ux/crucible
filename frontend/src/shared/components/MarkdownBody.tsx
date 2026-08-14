import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

/** 把导出的 report.md 渲染成可读正文。不启用 raw HTML，Agent 输出按不可信处理。 */
export function MarkdownBody({ source }: { source: string }) {
  return (
    <div className="crucible-markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{source}</ReactMarkdown>
    </div>
  )
}
