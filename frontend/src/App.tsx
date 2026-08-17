import { Route, Switch, Redirect } from 'wouter'
import { App as AntApp, Spin } from 'antd'
import { lazy, Suspense, type ReactNode } from 'react'
import { Providers } from './app/providers'

const LoginPage = lazy(() => import('./pages/LoginPage').then((module) => ({ default: module.LoginPage })))
const DashboardPage = lazy(() => import('./pages/DashboardPage').then((module) => ({ default: module.DashboardPage })))
const TasksPage = lazy(() => import('./pages/TasksPage').then((module) => ({ default: module.TasksPage })))
const TaskDetailPage = lazy(() => import('./pages/TaskDetailPage').then((module) => ({ default: module.TaskDetailPage })))
const ReportsPage = lazy(() => import('./pages/ReportsPage').then((module) => ({ default: module.ReportsPage })))
const ReportDetailPage = lazy(() => import('./pages/ReportDetailPage').then((module) => ({ default: module.ReportDetailPage })))
const SettingsPage = lazy(() => import('./pages/SettingsPage').then((module) => ({ default: module.SettingsPage })))
const ProjectsPage = lazy(() => import('./pages/ProjectsPage').then((module) => ({ default: module.ProjectsPage })))
const ProjectDetailPage = lazy(() => import('./pages/ProjectDetailPage').then((module) => ({ default: module.ProjectDetailPage })))
const LabsPage = lazy(() => import('./pages/LabsPage').then((module) => ({ default: module.LabsPage })))
const NotFoundPage = lazy(() => import('./pages/NotFoundPage').then((module) => ({ default: module.NotFoundPage })))

function RouteFallback() {
  return (
    <div style={{ minHeight: '100vh', display: 'grid', placeItems: 'center' }}>
      <Spin size="large" tip="页面加载中..." />
    </div>
  )
}

function RequireAuth({ children }: { children: ReactNode }) {
  const token = localStorage.getItem('crucible_token')
  if (!token) {
    return <Redirect to="/login" />
  }
  return <>{children}</>
}

export function App() {
  return (
    <Providers>
      <AntApp>
        <Suspense fallback={<RouteFallback />}>
          <Switch>
            <Route path="/login" component={LoginPage} />
            <Route path="/tasks/:id">
              <RequireAuth>
                <TaskDetailPage />
              </RequireAuth>
            </Route>
            <Route path="/tasks">
              <RequireAuth>
                <TasksPage />
              </RequireAuth>
            </Route>
            <Route path="/reports/:id">
              <RequireAuth>
                <ReportDetailPage />
              </RequireAuth>
            </Route>
            <Route path="/reports">
              <RequireAuth>
                <ReportsPage />
              </RequireAuth>
            </Route>
            <Route path="/projects/:id">
              <RequireAuth>
                <ProjectDetailPage />
              </RequireAuth>
            </Route>
            <Route path="/projects">
              <RequireAuth>
                <ProjectsPage />
              </RequireAuth>
            </Route>
            <Route path="/labs">
              <RequireAuth>
                <LabsPage />
              </RequireAuth>
            </Route>
            <Route path="/settings">
              <RequireAuth>
                <SettingsPage />
              </RequireAuth>
            </Route>
            <Route path="/">
              <RequireAuth>
                <DashboardPage />
              </RequireAuth>
            </Route>
            <Route component={NotFoundPage} />
          </Switch>
        </Suspense>
      </AntApp>
    </Providers>
  )
}
