import dockerfile from 'highlight.js/lib/languages/dockerfile'
import http from 'highlight.js/lib/languages/http'
import nginx from 'highlight.js/lib/languages/nginx'
import powershell from 'highlight.js/lib/languages/powershell'
import { common } from 'lowlight'
import type { Options } from 'rehype-highlight'

/** 报告 / PoC 常见语言；未知 fence 不抛错，只当纯文本。 */
export const markdownHighlightOptions: Options = {
  languages: {
    ...common,
    dockerfile,
    http,
    nginx,
    powershell,
  },
  aliases: {
    bash: ['sh', 'zsh'],
    python: ['py'],
    javascript: ['js', 'jsx'],
    typescript: ['ts', 'tsx'],
    yaml: ['yml'],
    xml: ['html', 'htm'],
    dockerfile: ['docker'],
    powershell: ['ps', 'ps1'],
  },
  plainText: ['text', 'txt', 'plain', 'output', 'console'],
}
