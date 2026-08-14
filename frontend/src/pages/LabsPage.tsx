import { AppLayout } from '../app/layout'
import { LabStacks } from '../features/lab/LabStacks'
import { PageContainer } from '../shared/components/PageContainer'
import { PageHeader } from '../shared/components/PageHeader'

export function LabsPage() {
  return (
    <AppLayout>
      <PageHeader title="靶场管理" subtitle="按项目查看和管理隔离靶场及其 compose 容器" />
      <PageContainer>
        <LabStacks />
      </PageContainer>
    </AppLayout>
  )
}
