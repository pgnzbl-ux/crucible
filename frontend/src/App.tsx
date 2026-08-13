import { Route, Switch, Redirect } from 'wouter'
import { App as AntApp } from 'antd'
import type { ReactNode } from 'react'
import { Providers } from './app/providers'
import { LoginPage } from './pages/LoginPage'
import { DashboardPage } from './pages/DashboardPage'
import { TasksPage } from './pages/TasksPage'
import { TaskDetailPage } from './pages/TaskDetailPage'
import { ReportsPage } from './pages/ReportsPage'
import { SettingsPage } from './pages/SettingsPage'
import { NotFoundPage } from './pages/NotFoundPage'

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
          <Route path="/reports">
            <RequireAuth>
              <ReportsPage />
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
      </AntApp>
    </Providers>
  )
}
