export type ProjectOptionRef = {
  ref_type: string
  ref_name: string
}

export type ProjectSelectSource = {
  id: string
  name: string
  git_url: string
  default_ref: string | null
  default_ref_type?: 'branch' | 'tag' | 'commit' | null
  source_refs?: ProjectOptionRef[]
}

export type GitProjectOption = {
  value: string
  label: string
  git_url: string
  ref_type?: 'branch' | 'tag' | 'commit'
  ref_name?: string
}

export type ProjectVersionOption = {
  value: string
  label: string
  ref_type: 'branch' | 'tag' | 'commit'
  ref_name?: string
  commit_sha?: string
}

const COMMIT_RE = /^[0-9a-fA-F]{7,40}$/
const TAG_RE = /^v?\d+\.\d+/i
const REF_TYPES = new Set(['branch', 'tag', 'commit'])

export function formatProjectVersionLabel(
  name: string,
  refType: string,
  refName: string,
  gitUrl: string,
): string {
  return `${name}：${refType}/${refName}  <${gitUrl}>`
}

export function formatVersionOptionLabel(
  refType: string,
  refName: string,
  commitSha?: string | null,
): string {
  const sha = commitSha ? ` · ${commitSha.slice(0, 7)}` : ''
  return `${refType}/${refName}${sha}`
}

export function projectVersionKey(refType: string, refName: string): string {
  return `${refType}::${refName}`
}

export function parseProjectVersionKey(key: string): {
  ref_type: 'branch' | 'tag' | 'commit'
  ref_name?: string
} {
  const [ref_type, ref_name = ''] = key.split('::', 2)
  const rt = ref_type as 'branch' | 'tag' | 'commit'
  if (rt === 'branch' && ref_name === 'HEAD') {
    return { ref_type: rt, ref_name: undefined }
  }
  return { ref_type: rt, ref_name: ref_name || undefined }
}

export function buildProjectVersionOptions(
  project: ProjectSelectSource,
  artifacts?: { ref_type: string; ref_name: string; commit_sha?: string }[],
): ProjectVersionOption[] {
  const seen = new Set<string>()
  const options: ProjectVersionOption[] = []

  const add = (ref_type: string, ref_name: string, commit_sha?: string) => {
    if (!REF_TYPES.has(ref_type) || !ref_name) return
    const key = projectVersionKey(ref_type, ref_name)
    if (seen.has(key)) return
    seen.add(key)
    const refType = ref_type as 'branch' | 'tag' | 'commit'
    const isDefaultHead = refType === 'branch' && ref_name === 'HEAD'
    options.push({
      value: key,
      label: formatVersionOptionLabel(refType, ref_name, commit_sha),
      ref_type: refType,
      ref_name: isDefaultHead ? undefined : ref_name,
      commit_sha,
    })
  }

  for (const row of artifacts ?? []) {
    add(row.ref_type, row.ref_name, row.commit_sha)
  }
  for (const ref of project.source_refs ?? []) {
    add(ref.ref_type, ref.ref_name)
  }
  if (!options.length) {
    const classified = project.default_ref_type
      ? {
          ref_type: project.default_ref_type,
          ref_name:
            project.default_ref_type === 'branch' && !(project.default_ref ?? '').trim()
              ? 'HEAD'
              : (project.default_ref ?? '').trim() || 'HEAD',
        }
      : classifyProjectRef(project.default_ref)
    add(classified.ref_type, classified.ref_name)
  }
  return options
}

export function matchProjectVersionKey(
  options: ProjectVersionOption[],
  refType?: string,
  refName?: string | null,
): string | undefined {
  if (!options.length) return undefined
  const normalizedRefName = (refName ?? '').trim()
  if (refType) {
    const hit = options.find((o) => {
      if (o.ref_type !== refType) return false
      const oName = o.ref_name ?? (o.ref_type === 'branch' ? 'HEAD' : '')
      const want = normalizedRefName || (refType === 'branch' ? 'HEAD' : '')
      return oName === want
    })
    if (hit) return hit.value
  }
  return options[0]?.value
}

export function classifyProjectRef(ref: string | null | undefined): ProjectOptionRef {
  const name = (ref || '').trim()
  if (!name || name.toUpperCase() === 'HEAD') return { ref_type: 'branch', ref_name: 'HEAD' }
  if (COMMIT_RE.test(name)) return { ref_type: 'commit', ref_name: name.toLowerCase() }
  if (name.startsWith('refs/tags/')) return { ref_type: 'tag', ref_name: name.slice('refs/tags/'.length) }
  if (name.startsWith('tags/')) return { ref_type: 'tag', ref_name: name.slice('tags/'.length) }
  if (TAG_RE.test(name) || name.startsWith('zentaopms_')) return { ref_type: 'tag', ref_name: name }
  return { ref_type: 'branch', ref_name: name }
}

export function projectDefaultRefLabel(project: ProjectSelectSource): string {
  if (project.default_ref_type) {
    const name = (project.default_ref ?? '').trim()
    if (project.default_ref_type === 'branch' && (!name || name.toUpperCase() === 'HEAD')) {
      return 'branch / HEAD'
    }
    if (name) return `${project.default_ref_type} / ${name}`
  }
  const classified = classifyProjectRef(project.default_ref)
  return `${classified.ref_type} / ${classified.ref_name}`
}

function refsForProject(project: ProjectSelectSource): ProjectOptionRef[] {
  const cached = (project.source_refs ?? []).filter((r) => REF_TYPES.has(r.ref_type) && r.ref_name)
  if (cached.length) return cached
  if (project.default_ref_type) {
    const name = (project.default_ref ?? '').trim()
    if (project.default_ref_type === 'branch' && (!name || name.toUpperCase() === 'HEAD')) {
      return [{ ref_type: 'branch', ref_name: 'HEAD' }]
    }
    if (name) return [{ ref_type: project.default_ref_type, ref_name: name }]
  }
  return [classifyProjectRef(project.default_ref)]
}

export function buildGitProjectOptions(projects: ProjectSelectSource[]): GitProjectOption[] {
  const options: GitProjectOption[] = []
  for (const project of projects) {
    for (const ref of refsForProject(project)) {
      const refType = ref.ref_type as 'branch' | 'tag' | 'commit'
      const isDefaultHead = refType === 'branch' && ref.ref_name === 'HEAD'
      options.push({
        value: `${project.id}::${refType}::${ref.ref_name}`,
        label: formatProjectVersionLabel(project.name, refType, ref.ref_name, project.git_url),
        git_url: project.git_url,
        ref_type: refType,
        ref_name: isDefaultHead ? undefined : ref.ref_name,
      })
    }
  }
  return options
}

export function filterGitProjectOption(input: string, option?: { label?: unknown; git_url?: string }): boolean {
  const q = input.trim().toLowerCase()
  if (!q) return true
  const label = String(option?.label ?? '').toLowerCase()
  const url = String(option?.git_url ?? '').toLowerCase()
  return label.includes(q) || url.includes(q)
}
