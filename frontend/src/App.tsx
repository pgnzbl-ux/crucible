import { Route, Switch, Redirect } from 'wouter'
import { App as AntApp } from 'antd'
import type { ReactNode } from 'react'
import { Providers } from './app/providers'
import { LoginPage } from './pages/LoginPage'
import { DashboardPage } from './pages/DashboardPage'
import { TasksPage } from './pages/TasksPage'
import { ReportsPage } from './pages/ReportsPage'
import { SettingsPage } from './pages/SettingsPage'

// 路由守卫：无 token → /login（P0-3 JWT 闭环）
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
          <Route>404 — 页面未找到</Route>
        </Switch>
      </AntApp>
    </Providers>
  )
}
