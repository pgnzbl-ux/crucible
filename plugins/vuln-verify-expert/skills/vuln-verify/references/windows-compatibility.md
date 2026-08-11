# Windows PowerShell Compatibility Guide

## Known Issues and Workarounds

### 1. Quote Escaping in PowerShell

PowerShell mangles `\"` in JSON strings:

```powershell
# WRONG: PowerShell may eat quotes, body becomes empty
curl -X POST /api -d '{"key":"value"}'

# CORRECT: Use body file
echo '{"key":"value"}' > body.json
curl -X POST /api -d "@body.json"
```

### 2. node -e Inline Scripts

Multiline scripts don't work in PowerShell:

```powershell
# WRONG
node -e "const x = await fetch('/api'); console.log(x)"

# CORRECT: Use .js file
node script.js
```

### 3. Unicode in stdout

GBK encoding can't print some characters:

```python
# Add at top of Python scripts
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
```

### 4. Playwright Browser Download Timeout

183MB download may fail:

```javascript
// Fallback: use system Chrome
const browser = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: false
});
```

### 5. JSON Parsing in PowerShell

PowerShell doesn't have `head`/`tail`:

```powershell
# WRONG: head/tail not available
curl -s /api | head -200

# CORRECT: Use Python
curl -s /api -o response.json
python -c "import json; print(json.load(open('response.json')))"
```

### 6. /tmp Path on Git Bash

Git Bash's `/tmp` doesn't map to Windows Python's path. Use project-relative
paths or absolute Windows paths for file sharing between bash and python.
