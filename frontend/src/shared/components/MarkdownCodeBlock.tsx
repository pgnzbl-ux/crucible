import { CheckOutlined, CopyOutlined } from '@ant-design/icons'
import {
  Children,
  isValidElement,
  useEffect,
  useMemo,
  useState,
  type ComponentPropsWithoutRef,
  type ReactNode,
} from 'react'
import type { ExtraProps } from 'react-markdown'

function collectText(node: ReactNode): string {
  if (node == null || typeof node === 'boolean') return ''
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(collectText).join('')
  if (isValidElement<{ children?: ReactNode }>(node)) {
    return collectText(node.props.children)
  }
  return ''
}

function languageFrom(children: ReactNode): string {
  const child = Children.toArray(children).find((n) => isValidElement(n))
  if (!isValidElement<{ className?: string }>(child)) return ''
  const className = child.props.className ?? ''
  const match = className.match(/(?:language|lang)-([\w+-]+)/)
  return match?.[1]?.toLowerCase() ?? ''
}

async function copyToClipboard(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    /* 非安全上下文等，走回退 */
  }
  try {
    const el = document.createElement('textarea')
    el.value = text
    el.setAttribute('readonly', '')
    el.style.position = 'fixed'
    el.style.left = '-9999px'
    document.body.appendChild(el)
    el.select()
    const ok = document.execCommand('copy')
    el.remove()
    return ok
  } catch {
    return false
  }
}

type PreProps = ComponentPropsWithoutRef<'pre'> & ExtraProps

/** 围栏代码：语言标 + 复制。高亮由 rehype-highlight 在 AST 上完成。 */
export function MarkdownCodeBlock({ children, node: _node, ...props }: PreProps) {
  const [copied, setCopied] = useState(false)
  const lang = languageFrom(children)
  const code = useMemo(() => collectText(children).replace(/\n$/, ''), [children])

  useEffect(() => {
    if (!copied) return
    const id = window.setTimeout(() => setCopied(false), 1600)
    return () => window.clearTimeout(id)
  }, [copied])

  const onCopy = async () => {
    if (!code) return
    const ok = await copyToClipboard(code)
    if (ok) setCopied(true)
  }

  return (
    <div className="crucible-codeblock">
      <div className="crucible-codeblock__bar">
        <span className="crucible-codeblock__lang">{lang || 'text'}</span>
        <button
          type="button"
          className="crucible-codeblock__copy"
          onClick={onCopy}
          aria-label="复制代码"
        >
          {copied ? <CheckOutlined /> : <CopyOutlined />}
          {copied ? '已复制' : '复制'}
        </button>
      </div>
      <pre
        {...props}
        className={['crucible-codeblock__pre', props.className].filter(Boolean).join(' ')}
      >
        {children}
      </pre>
    </div>
  )
}
