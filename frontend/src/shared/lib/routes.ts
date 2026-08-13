export interface RouteMeta {
  title: string
  breadcrumb: { label: string; path?: string }[]
}

const ROUTE_META: Record<string, RouteMeta> = {
  '/': {
    title: '工作台',
    breadcrumb: [{ label: '工作台' }],
  },
  '/tasks': {
    title: '任务管理',
    breadcrumb: [{ label: '业务' }, { label: '任务管理' }],
  },
  '/reports': {
    title: '验证报告',
    breadcrumb: [{ label: '业务' }, { label: '验证报告' }],
  },
  '/settings': {
    title: '设置',
    breadcrumb: [{ label: '系统' }, { label: '设置' }],
  },
}

export function getRouteMeta(pathname: string): RouteMeta {
  if (pathname.startsWith('/tasks/') && pathname !== '/tasks') {
    const id = pathname.split('/')[2]
    return {
      title: '任务详情',
      breadcrumb: [
        { label: '业务' },
        { label: '任务管理', path: '/tasks' },
        { label: id ? `任务 ${id.slice(0, 8)}` : '详情' },
      ],
    }
  }
  const base = pathname.split('?')[0]
  return ROUTE_META[base] ?? { title: 'Crucible', breadcrumb: [{ label: 'Crucible' }] }
}
