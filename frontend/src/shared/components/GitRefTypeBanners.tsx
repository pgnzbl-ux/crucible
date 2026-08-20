import { BranchesOutlined, NumberOutlined, TagOutlined } from '@ant-design/icons'

export type GitRefType = 'branch' | 'tag' | 'commit'

export const GIT_REF_PLACEHOLDERS: Record<GitRefType, string> = {
  branch: 'main / master / develop（留空=默认分支）',
  tag: 'v1.0.0 / zentaopms_22.4_20260730',
  commit: '完整或短 SHA（7–40 位十六进制）',
}

const OPTIONS: {
  value: GitRefType
  title: string
  hint: string
  icon: typeof BranchesOutlined
}[] = [
  {
    value: 'branch',
    title: '分支',
    hint: '跟踪远端分支 tip',
    icon: BranchesOutlined,
  },
  {
    value: 'tag',
    title: '标签',
    hint: '固定发行版本',
    icon: TagOutlined,
  },
  {
    value: 'commit',
    title: '提交',
    hint: '精确到 SHA',
    icon: NumberOutlined,
  },
]

interface GitRefTypeBannersProps {
  value?: GitRefType | null
  onChange?: (value: GitRefType) => void
  disabled?: boolean
}

/** branch / tag / commit 三选一横幅，替代下拉框。 */
export function GitRefTypeBanners({ value, onChange, disabled }: GitRefTypeBannersProps) {
  return (
    <div className="crucible-ref-banners" role="radiogroup" aria-label="引用类型">
      {OPTIONS.map((opt) => {
        const selected = value === opt.value
        const Icon = opt.icon
        return (
          <button
            key={opt.value}
            type="button"
            role="radio"
            aria-checked={selected}
            disabled={disabled}
            className={
              selected
                ? 'crucible-ref-banner crucible-ref-banner--selected'
                : 'crucible-ref-banner'
            }
            onClick={() => onChange?.(opt.value)}
          >
            <span className="crucible-ref-banner-icon">
              <Icon />
            </span>
            <span className="crucible-ref-banner-body">
              <span className="crucible-ref-banner-title">{opt.title}</span>
              <span className="crucible-ref-banner-code">{opt.value}</span>
              <span className="crucible-ref-banner-hint">{opt.hint}</span>
            </span>
          </button>
        )
      })}
    </div>
  )
}
