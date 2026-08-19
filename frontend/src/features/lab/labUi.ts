export function canMutateLab(liveTaskCount: number): boolean {
  return liveTaskCount === 0
}

export function canStartLab(status: string, liveTaskCount: number): boolean {
  return status === 'stopped' && liveTaskCount === 0
}

export function canStopLab(status: string, liveTaskCount: number): boolean {
  return status === 'ready' && liveTaskCount === 0
}

const REBUILDABLE = new Set(['creating', 'ready', 'stopped', 'failed', 'expired', 'destroyed'])

export function canRebuildLab(status: string, liveTaskCount: number): boolean {
  return REBUILDABLE.has(status) && liveTaskCount === 0
}

const DESTROYABLE = new Set(['creating', 'ready', 'stopped', 'failed', 'expired'])

export function canDestroyLab(status: string, liveTaskCount: number): boolean {
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
