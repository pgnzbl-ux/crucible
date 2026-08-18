export const TASK_CREATE_LOCK_ID = 'task-create'

export type TaskMutationKind = 'cancel' | 'retry' | 'delete'

export interface TaskMutationCacheOps {
  remove: string[][]
  invalidate: string[][]
}

export function taskMutationCacheOps(taskId: string, kind: TaskMutationKind): TaskMutationCacheOps {
  if (kind === 'cancel') {
    return {
      remove: [],
      invalidate: [['task', taskId], ['tasks'], ['task-stats'], ['run-nodes', taskId]],
    }
  }
  if (kind === 'retry') {
    return {
      remove: [['task-report', taskId]],
      invalidate: [
        ['task', taskId],
        ['tasks'],
        ['task-stats'],
        ['run-nodes', taskId],
        ['task-events', taskId],
      ],
    }
  }
  return {
    remove: [
      ['task', taskId],
      ['run-nodes', taskId],
      ['task-events', taskId],
      ['task-report', taskId],
    ],
    invalidate: [['tasks'], ['task-stats']],
  }
}

type QueryCache = {
  invalidateQueries: (opts: { queryKey: string[] }) => unknown
  removeQueries: (opts: { queryKey: string[] }) => unknown
}

export function applyTaskMutationCache(
  qc: QueryCache,
  taskId: string,
  kind: TaskMutationKind,
): void {
  const ops = taskMutationCacheOps(taskId, kind)
  for (const queryKey of ops.remove) {
    qc.removeQueries({ queryKey })
  }
  for (const queryKey of ops.invalidate) {
    qc.invalidateQueries({ queryKey })
  }
}
