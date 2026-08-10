import { Route, Switch } from 'wouter'
import { App as AntApp } from 'antd'
import { Providers } from './app/providers'
import { LoginPage } from './pages/LoginPage'
import { DashboardPage } from './pages/DashboardPage'
import { TasksPage } from './pages/TasksPage'
import { ReportsPage } from './pages/ReportsPage'
import { SettingsPage } from './pages/SettingsPage'

export function App() {
  return (
    <Providers>
      <AntApp>
        <Switch>
          <Route path="/login" component={LoginPage} />
          <Route path="/tasks" component={TasksPage} />
          <Route path="/reports" component={ReportsPage} />
          <Route path="/settings" component={SettingsPage} />
          <Route path="/" component={DashboardPage} />
          <Route>404 — 页面未找到</Route>
        </Switch>
      </AntApp>
    </Providers>
  )
}
