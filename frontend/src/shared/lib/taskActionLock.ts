const locks = new Set<string>()

export function tryLockTaskAction(taskId: string): boolean {
  if (!taskId || locks.has(taskId)) return false
  locks.add(taskId)
  return true
}

export function unlockTaskAction(taskId: string): void {
  locks.delete(taskId)
}

export function resetTaskActionLocks(): void {
  locks.clear()
}
