import type { ComponentPropsWithoutRef } from 'react'
import ReactMarkdown, { type Components, type ExtraProps } from 'react-markdown'
import rehypeHighlight from 'rehype-highlight'
import remarkGfm from 'remark-gfm'

import { markdownHighlightOptions } from '../lib/markdownHighlight'
import { markdownUrlTransform } from '../lib/safeUrl'
import { MarkdownCodeBlock } from './MarkdownCodeBlock'

function MarkdownTable({
  node: _node,
  ...props
}: ComponentPropsWithoutRef<'table'> & ExtraProps) {
  return (
    <div className="crucible-markdown__table-wrap">
      <table {...props} />
    </div>
  )
}

function MarkdownImage({
  node: _node,
  alt,
  className,
  ...props
}: ComponentPropsWithoutRef<'img'> & ExtraProps) {
  return (
    <img
      alt={alt ?? ''}
      className={['crucible-markdown__img', className].filter(Boolean).join(' ')}
      {...props}
    />
  )
}

const markdownComponents: Components = {
  pre: MarkdownCodeBlock,
  table: MarkdownTable,
  img: MarkdownImage,
}

/** 把导出的 report.md 渲染成可读正文。不启用 raw HTML，Agent 输出按不可信处理。 */
export function MarkdownBody({ source }: { source: string }) {
  return (
    <div className="crucible-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[[rehypeHighlight, markdownHighlightOptions]]}
        urlTransform={markdownUrlTransform}
        components={markdownComponents}
      >
        {source}
      </ReactMarkdown>
    </div>
  )
}
