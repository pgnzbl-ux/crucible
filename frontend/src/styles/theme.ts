import { theme } from 'antd'
import type { ThemeConfig } from 'antd'

/**
 * Crucible 中性安全风主题
 * 石墨灰表面层级 + 克制的钢蓝主色 + 绿色成功 / 红色告警
 * 与 styles/design-tokens.css 保持同源（实现以本文件为准）
 */
export const crucibleTheme: ThemeConfig = {
  algorithm: theme.darkAlgorithm,
  token: {
    // 品牌与语义色
    colorPrimary: '#5b7fd4',
    colorInfo: '#5b7fd4',
    colorSuccess: '#22a06b',
    colorWarning: '#d97706',
    colorError: '#d64545',
    colorLink: '#6f90e0',
    // 表面层级（石墨灰）
    colorBgBase: '#0f1115',
    colorBgContainer: '#15181f',
    colorBgElevated: '#1b2029',
    colorBgLayout: '#0f1115',
    colorBorder: 'rgba(255,255,255,0.08)',
    colorBorderSecondary: 'rgba(255,255,255,0.06)',
    // 文本
    colorText: '#e6e8ec',
    colorTextSecondary: '#9aa1ac',
    colorTextTertiary: '#6f7683',
    // 形状与排版
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
  },
  components: {
    Layout: {
      siderBg: '#0c0e12',
      headerBg: 'rgba(15,17,21,0.85)',
      headerColor: '#e6e8ec',
      headerHeight: 56,
      headerPadding: '0 24px',
      bodyBg: '#0f1115',
    },
    Menu: {
      darkItemBg: '#0c0e12',
      darkSubMenuItemBg: 'rgba(255,255,255,0.02)',
      darkItemSelectedBg: 'rgba(91,127,212,0.18)',
      darkItemSelectedColor: '#9db4e8',
      darkItemHoverBg: 'rgba(255,255,255,0.05)',
      darkItemColor: '#aab2c0',
      itemBorderRadius: 8,
      itemMarginInline: 8,
    },
    Table: {
      headerBg: '#1b2029',
      headerColor: '#9aa1ac',
      headerSplitColor: 'transparent',
      rowHoverBg: 'rgba(255,255,255,0.04)',
      cellPaddingBlock: 12,
      cellPaddingInline: 16,
      borderColor: 'rgba(255,255,255,0.06)',
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
      defaultBg: '#1b2029',
      defaultBorderColor: 'rgba(255,255,255,0.12)',
    },
    Segmented: {
      itemSelectedBg: '#1b2029',
    },
  },
}
