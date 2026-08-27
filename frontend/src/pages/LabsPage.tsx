import { LabStacks } from '../features/lab/LabStacks'
import { PageContainer } from '../shared/components/PageContainer'
import { PageHeader } from '../shared/components/PageHeader'

export function LabsPage() {
  return (
    <>
      <PageHeader title="验证环境" subtitle="管理动态终认使用的隔离靶场及其容器" />
      <PageContainer>
        <LabStacks />
      </PageContainer>
    </>
  )
}
