export function canMutateLab(liveTaskCount: number): boolean {
  return liveTaskCount === 0
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

