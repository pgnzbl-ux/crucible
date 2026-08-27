# CWE-79 跨站脚本(XSS) 微评分表

## 高风险模式
- 模板输出未转义（|safe、v-html、dangerouslySetInnerHTML）
- innerHTML/insertAdjacentHTML 接收用户数据
- HTTP 响应反射用户输入且 Content-Type 猜测

## 安全惯用法
- 上下文相关的自动转义（默认模板转义）
- textContent 而非 innerHTML
- CSP 严格 nonce 作为纵深（非唯一防线）

## 伪消毒器（看似净化，实则可绕过——误判重灾区）
- 仅过滤 <script> 标签
- HTML encode 后插入 JS/URL 上下文（上下文错配）
- 服务端转义却存库后在前端二次 decode

## 引导问题（依次回答后再下结论）
1. 污点数据的真实来源是什么？是否攻击者可控？
2. 从入口到危险点之间有哪些净化/校验？对照上方伪消毒器清单逐条排查。
3. 该路径是否真实可达（挂在有效路由/入口之后，且配置启用）？
