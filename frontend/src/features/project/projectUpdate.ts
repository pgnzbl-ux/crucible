import type { GitRefType } from '../../shared/components/GitRefTypeBanners'
import type { Project } from '../../shared/lib/api'
import { classifyProjectRef } from '../task/lib/projectSelectOptions'

export type ProjectEditValues = {
  name: string
  description?: string
  default_ref_type?: GitRefType
  default_ref?: string
}

export type ProjectUpdateBody = {
  name: string
  description: string
  default_ref?: string
  default_ref_type?: GitRefType
}

export function isUploadProject(project: Pick<Project, 'source_type'>): boolean {
  return project.source_type === 'local_upload'
}

export function projectEditInitialValues(project: Project): ProjectEditValues {
  if (isUploadProject(project)) {
    return {
      name: project.name,
      description: project.description ?? '',
    }
  }
  const classified = project.default_ref_type
    ? {
        ref_type: project.default_ref_type,
        ref_name: (project.default_ref ?? '').trim(),
      }
    : classifyProjectRef(project.default_ref)
  const refType = classified.ref_type as GitRefType
  const refName = classified.ref_name
  const default_ref =
    refType === 'branch' && (!refName || refName.toUpperCase() === 'HEAD') ? '' : refName
  return {
    name: project.name,
    description: project.description ?? '',
    default_ref_type: refType,
    default_ref,
  }
}

export function buildProjectUpdatePayload(
  project: Pick<Project, 'source_type'>,
  values: ProjectEditValues,
): ProjectUpdateBody {
  const payload: ProjectUpdateBody = {
    name: values.name.trim(),
    description: (values.description ?? '').trim(),
  }
  if (!isUploadProject(project)) {
    payload.default_ref_type = values.default_ref_type ?? 'branch'
    payload.default_ref = (values.default_ref ?? '').trim()
  }
  return payload
}
