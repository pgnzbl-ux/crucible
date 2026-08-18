/** 路由懒加载时留在内容区，不卸掉侧栏/顶栏。 */
export function RouteContentFallback() {
  return (
    <div className="crucible-route-fallback" role="status" aria-live="polite" aria-label="页面加载中">
      <div className="crucible-route-progress" aria-hidden="true">
        <span className="crucible-route-progress-bar" />
      </div>
    </div>
  )
}
