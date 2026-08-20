export function canMutateLab(liveTaskCount: number): boolean {
  return liveTaskCount === 0
}

export function canStartLab(status: string, liveTaskCount: number): boolean {
  return (status === 'stopped' || status === 'expired') && liveTaskCount === 0
}

export function showStartLab(status: string, liveTaskCount: number): boolean {
  return canStartLab(status, liveTaskCount)
}

export function canStopLab(status: string, liveTaskCount: number): boolean {
  return status === 'ready' && liveTaskCount === 0
}

export function showStopLab(status: string, liveTaskCount: number): boolean {
  return canStopLab(status, liveTaskCount)
}

const REBUILDABLE = new Set(['creating', 'ready', 'stopped', 'failed', 'expired', 'destroyed'])

export function canRebuildLab(status: string, liveTaskCount: number): boolean {
  if (status === 'rebuilding' || liveTaskCount > 0) return false
  return REBUILDABLE.has(status)
}

export function showRebuildLab(status: string, liveTaskCount: number): boolean {
  return canRebuildLab(status, liveTaskCount)
}

const DESTROYABLE = new Set(['creating', 'ready', 'stopped', 'failed', 'expired'])

export function canDestroyLab(status: string, liveTaskCount: number): boolean {
  if (status === 'rebuilding') return false
  if (status === 'creating') return true
  return DESTROYABLE.has(status) && liveTaskCount === 0
}

export function showDestroyLab(status: string, liveTaskCount: number): boolean {
  if (status === 'destroyed' || status === 'rebuilding') return false
  if (status === 'creating') return true
  return DESTROYABLE.has(status) && liveTaskCount === 0
}

export function isLabTtlActive(status: string): boolean {
  return status === 'ready' || status === 'stopped'
}

const TRANSIENT_LAB_STATUSES = new Set(['creating', 'starting', 'rebuilding'])
const RUNTIME_DRIFT_STATUSES = new Set(['expired', 'stopped', 'failed'])

export function shouldPollLabs(
  groups: Array<{ labs: Array<{ status: string; live_task_count?: number }> }> | undefined,
): boolean {
  return (groups ?? []).some((group) =>
    group.labs.some(
      (lab) =>
        TRANSIENT_LAB_STATUSES.has(lab.status) ||
        RUNTIME_DRIFT_STATUSES.has(lab.status) ||
        (lab.live_task_count ?? 0) > 0,
    ),
  )
}
