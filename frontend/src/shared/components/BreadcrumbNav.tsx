import { Breadcrumb } from 'antd'
import { Link, useLocation } from 'wouter'
import { getRouteMeta } from '../lib/routes'

export function BreadcrumbNav() {
  const [location] = useLocation()
  const meta = getRouteMeta(location)

  const items = meta.breadcrumb.map((item, i) => ({
    key: String(i),
    title: item.path ? <Link to={item.path}>{item.label}</Link> : item.label,
  }))

  return <Breadcrumb items={items} />
}
