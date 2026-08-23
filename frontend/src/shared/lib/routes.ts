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
    title: '代码审计',
    breadcrumb: [{ label: '业务' }, { label: '代码审计' }],
  },
  '/projects': {
    title: '项目资产',
    breadcrumb: [{ label: '业务' }, { label: '项目资产' }],
  },
  '/labs': {
    title: '验证环境',
    breadcrumb: [{ label: '业务' }, { label: '验证环境' }],
  },
  '/reports': {
    title: '审计报告',
    breadcrumb: [{ label: '业务' }, { label: '审计报告' }],
  },
  '/findings': {
    title: '漏洞线索',
    breadcrumb: [{ label: '业务' }, { label: '漏洞线索' }],
  },
  '/settings': {
    title: '设置',
    breadcrumb: [{ label: '系统' }, { label: '设置' }],
  },
}

export function getRouteMeta(pathname: string): RouteMeta {
  if (pathname.startsWith('/reports/') && pathname !== '/reports') {
    return {
      title: '报告详情',
      breadcrumb: [
        { label: '业务' },
        { label: '审计报告', path: '/reports' },
        { label: '详情' },
      ],
    }
  }
  if (pathname.startsWith('/projects/') && pathname !== '/projects') {
    return {
      title: '项目详情',
      breadcrumb: [
        { label: '业务' },
        { label: '项目资产', path: '/projects' },
        { label: '详情' },
      ],
    }
  }
  if (pathname.startsWith('/tasks/') && pathname !== '/tasks') {
    const id = pathname.split('/')[2]
    return {
      title: '审计详情',
      breadcrumb: [
        { label: '业务' },
        { label: '代码审计', path: '/tasks' },
        { label: id ? `运行 ${id.slice(0, 8)}` : '详情' },
      ],
    }
  }
  if (pathname.startsWith('/findings/') && pathname !== '/findings') {
    return {
      title: '线索详情',
      breadcrumb: [
        { label: '业务' },
        { label: '漏洞线索', path: '/findings' },
        { label: '详情' },
      ],
    }
  }
  const base = pathname.split('?')[0]
  return ROUTE_META[base] ?? { title: 'Crucible', breadcrumb: [{ label: 'Crucible' }] }
}
