import { Route, Switch, Redirect, useLocation } from 'wouter'
import { App as AntApp } from 'antd'
import { lazy, Suspense, type ReactNode } from 'react'
import { Providers } from './app/providers'
import { AppLayout } from './app/layout'
import { RouteContentFallback } from './shared/components/RouteContentFallback'

const LoginPage = lazy(() => import('./pages/LoginPage').then((module) => ({ default: module.LoginPage })))
const DashboardPage = lazy(() => import('./pages/DashboardPage').then((module) => ({ default: module.DashboardPage })))
const TasksPage = lazy(() => import('./pages/TasksPage').then((module) => ({ default: module.TasksPage })))
const TaskDetailPage = lazy(() => import('./pages/TaskDetailPage').then((module) => ({ default: module.TaskDetailPage })))
const FindingsPage = lazy(() => import('./pages/FindingsPage').then((module) => ({ default: module.FindingsPage })))
const FindingDetailPage = lazy(() => import('./pages/FindingDetailPage').then((module) => ({ default: module.FindingDetailPage })))
const ReportsPage = lazy(() => import('./pages/ReportsPage').then((module) => ({ default: module.ReportsPage })))
const ReportDetailPage = lazy(() => import('./pages/ReportDetailPage').then((module) => ({ default: module.ReportDetailPage })))
const SettingsPage = lazy(() => import('./pages/SettingsPage').then((module) => ({ default: module.SettingsPage })))
const ProjectsPage = lazy(() => import('./pages/ProjectsPage').then((module) => ({ default: module.ProjectsPage })))
const ProjectDetailPage = lazy(() => import('./pages/ProjectDetailPage').then((module) => ({ default: module.ProjectDetailPage })))
const LabsPage = lazy(() => import('./pages/LabsPage').then((module) => ({ default: module.LabsPage })))
const NotFoundPage = lazy(() => import('./pages/NotFoundPage').then((module) => ({ default: module.NotFoundPage })))

function RequireAuth({ children }: { children: ReactNode }) {
  const token = localStorage.getItem('crucible_token')
  if (!token) {
    return <Redirect to="/login" />
  }
  return <>{children}</>
}

function AuthenticatedShell() {
  const [location] = useLocation()
  const fill = location.startsWith('/tasks/') || location.startsWith('/reports/')
  return (
    <AppLayout fill={fill}>
      <Suspense fallback={<RouteContentFallback />}>
        <Switch>
          <Route path="/findings/:id" component={FindingDetailPage} />
          <Route path="/findings" component={FindingsPage} />
          <Route path="/tasks/:id" component={TaskDetailPage} />
          <Route path="/tasks" component={TasksPage} />
          <Route path="/reports/:id" component={ReportDetailPage} />
          <Route path="/reports" component={ReportsPage} />
          <Route path="/projects/:id" component={ProjectDetailPage} />
          <Route path="/projects" component={ProjectsPage} />
          <Route path="/labs" component={LabsPage} />
          <Route path="/settings" component={SettingsPage} />
          <Route path="/" component={DashboardPage} />
          <Route component={NotFoundPage} />
        </Switch>
      </Suspense>
    </AppLayout>
  )
}

export function App() {
  return (
    <Providers>
      <AntApp>
        <Switch>
          <Route path="/login">
            <Suspense fallback={<RouteContentFallback />}>
              <LoginPage />
            </Suspense>
          </Route>
          <Route>
            <RequireAuth>
              <AuthenticatedShell />
            </RequireAuth>
          </Route>
        </Switch>
      </AntApp>
    </Providers>
  )
}
