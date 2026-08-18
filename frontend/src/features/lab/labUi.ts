export function canMutateLab(liveTaskCount: number): boolean {
  return liveTaskCount === 0
}

const TRANSIENT_LAB_STATUSES = new Set(['creating', 'starting', 'rebuilding'])

export function shouldPollLabs(
  groups: Array<{ labs: Array<{ status: string }> }> | undefined,
): boolean {
  return (groups ?? []).some((group) =>
    group.labs.some((lab) => TRANSIENT_LAB_STATUSES.has(lab.status)),
  )
}

