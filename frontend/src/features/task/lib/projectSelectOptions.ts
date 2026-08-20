export type ProjectOptionRef = {
  ref_type: string
  ref_name: string
}

export type ProjectSelectSource = {
  id: string
  name: string
  git_url: string
  default_ref: string | null
  source_refs?: ProjectOptionRef[]
}

export type GitProjectOption = {
  value: string
  label: string
  git_url: string
  ref_type?: 'branch' | 'tag' | 'commit'
  ref_name?: string
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

export function classifyProjectRef(ref: string | null | undefined): ProjectOptionRef {
  const name = (ref || '').trim()
  if (!name || name.toUpperCase() === 'HEAD') return { ref_type: 'branch', ref_name: 'HEAD' }
  if (COMMIT_RE.test(name)) return { ref_type: 'commit', ref_name: name.toLowerCase() }
  if (name.startsWith('refs/tags/')) return { ref_type: 'tag', ref_name: name.slice('refs/tags/'.length) }
  if (name.startsWith('tags/')) return { ref_type: 'tag', ref_name: name.slice('tags/'.length) }
  if (TAG_RE.test(name) || name.startsWith('zentaopms_')) return { ref_type: 'tag', ref_name: name }
  return { ref_type: 'branch', ref_name: name }
}

function refsForProject(project: ProjectSelectSource): ProjectOptionRef[] {
  const cached = (project.source_refs ?? []).filter((r) => REF_TYPES.has(r.ref_type) && r.ref_name)
  if (cached.length) return cached
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
