import { Space, Tag, Typography } from 'antd'

import { parseInitialCreds } from '../lib/nodeOutput'

const { Link, Text } = Typography

interface EnvReadyDetailProps {
  output: Record<string, unknown> | null | undefined
}

function str(v: unknown): string {
  return typeof v === 'string' ? v.trim() : ''
}

/** 靶场地址 + 登录凭据。凭据缺失和"确实免登录"要分开说，否则看着像界面丢了数据。 */
export function EnvReadyDetail({ output }: EnvReadyDetailProps) {
  const o = output ?? {}
  const url = str(o.target_url)
  const creds = parseInitialCreds(o.initial_creds)

  return (
    <div className="crucible-env-ready">
      <Space size={[8, 4]} wrap>
        {url ? (
          <Link href={url} target="_blank" rel="noreferrer">
            {url}
          </Link>
        ) : (
          <Text type="secondary">靶场已就绪</Text>
        )}
        {o.reused === true ? <Tag>复用靶场</Tag> : null}
      </Space>

      {creds.state === 'creds' ? (
        <Space size={[12, 4]} wrap>
          {creds.username ? (
            <Space size={4}>
              <Text type="secondary">账号</Text>
              <Text code copyable={{ text: creds.username }}>
                {creds.username}
              </Text>
            </Space>
          ) : null}
          {creds.password ? (
            <Space size={4}>
              <Text type="secondary">密码</Text>
              <Text code copyable={{ text: creds.password }}>
                {creds.password}
              </Text>
            </Space>
          ) : null}
          {creds.loginUrl ? (
            <Space size={4}>
              <Text type="secondary">登录入口</Text>
              <Text code>{creds.loginUrl}</Text>
            </Space>
          ) : null}
        </Space>
      ) : (
        <Space size={[8, 4]} wrap>
          <Tag color={creds.state === 'no_auth' ? 'blue' : 'default'}>
            {creds.state === 'no_auth' ? '免登录' : '无预设凭据'}
          </Tag>
          <Text type="secondary">
            {creds.note ||
              (creds.state === 'no_auth'
                ? '该靶场无需登录即可访问'
                : 'Agent 未在项目里找到预设账号，如需登录请自行确认')}
          </Text>
        </Space>
      )}

      {creds.state === 'creds' && creds.note ? (
        <Text type="secondary">{creds.note}</Text>
      ) : null}
    </div>
  )
}
