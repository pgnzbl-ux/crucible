import { theme } from 'antd'
import type { ThemeConfig } from 'antd'

/**
 * Crucible 浅色企业后台主题（Ant Design Pro 风格）
 * 白底表面 + 标准蓝主色 + 绿色成功 / 红色告警
 * 与 styles/design-tokens.css 保持同源（实现以本文件为准）
 */

/** 顶栏高度。侧栏品牌区共用同一数值，CSS 侧见 --crucible-header-height。 */
export const HEADER_HEIGHT = 56
export const SIDER_WIDTH = 224
export const SIDER_COLLAPSED_WIDTH = 64

export const crucibleTheme: ThemeConfig = {
  algorithm: theme.defaultAlgorithm,
  token: {
    colorPrimary: '#1677ff',
    colorInfo: '#1677ff',
    colorSuccess: '#52c41a',
    colorWarning: '#faad14',
    colorError: '#ff4d4f',
    colorLink: '#1677ff',
    colorBgBase: '#f5f5f5',
    colorBgContainer: '#ffffff',
    colorBgElevated: '#ffffff',
    colorBgLayout: '#f0f2f5',
    colorBorder: '#f0f0f0',
    colorBorderSecondary: '#f0f0f0',
    colorText: 'rgba(0, 0, 0, 0.88)',
    colorTextSecondary: 'rgba(0, 0, 0, 0.65)',
    colorTextTertiary: 'rgba(0, 0, 0, 0.45)',
    borderRadius: 8,
    borderRadiusLG: 12,
    borderRadiusSM: 6,
    fontSize: 14,
    fontSizeSM: 12,
    fontSizeLG: 16,
    fontSizeHeading3: 20,
    fontSizeHeading4: 16,
    fontFamily:
      '"IBM Plex Sans", "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", -apple-system, BlinkMacSystemFont, sans-serif',
    fontFamilyCode: '"JetBrains Mono", "SFMono-Regular", Consolas, "Liberation Mono", monospace',
    controlHeight: 32,
    boxShadow: '0 1px 2px 0 rgba(0, 0, 0, 0.03), 0 1px 6px -1px rgba(0, 0, 0, 0.02), 0 2px 4px 0 rgba(0, 0, 0, 0.02)',
    boxShadowSecondary:
      '0 6px 16px 0 rgba(0, 0, 0, 0.08), 0 3px 6px -4px rgba(0, 0, 0, 0.12), 0 9px 28px 8px rgba(0, 0, 0, 0.05)',
  },
  components: {
    Layout: {
      siderBg: '#ffffff',
      headerBg: '#ffffff',
      headerColor: 'rgba(0, 0, 0, 0.88)',
      headerHeight: HEADER_HEIGHT,
      headerPadding: '0 20px',
      bodyBg: '#f0f2f5',
    },
    Menu: {
      itemBg: 'transparent',
      itemSelectedBg: '#e6f4ff',
      itemSelectedColor: '#1677ff',
      itemHoverBg: 'rgba(0, 0, 0, 0.04)',
      itemColor: 'rgba(0, 0, 0, 0.65)',
      itemBorderRadius: 8,
      itemMarginInline: 8,
      itemMarginBlock: 2,
      itemHeight: 38,
      subMenuItemBg: 'transparent',
      activeBarBorderWidth: 0,
    },
    Table: {
      headerBg: '#fafafa',
      headerColor: 'rgba(0, 0, 0, 0.88)',
      headerSplitColor: 'transparent',
      rowHoverBg: '#fafafa',
      cellPaddingBlock: 12,
      cellPaddingInline: 16,
      borderColor: '#f0f0f0',
    },
    Card: {
      headerBg: 'transparent',
      headerFontSize: 15,
      bodyPadding: 20,
      paddingLG: 20,
      borderRadiusLG: 12,
    },
    Drawer: {
      paddingLG: 24,
    },
    Tag: {
      borderRadiusSM: 6,
    },
    Statistic: {
      contentFontSize: 28,
    },
    Button: {
      fontWeight: 500,
    },
    Tabs: {
      cardBg: '#fafafa',
    },
  },
}
