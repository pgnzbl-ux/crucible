# backend/semgrep_rules

Semgrep 规则包根目录。由 ``SCANNER_SEMGREP_RULES_DIR`` 指向（见 ``backend/.env``）。

```text
backend/semgrep_rules/
  php/ python/ go/ java/ javascript/ typescript/   # 社区语言树
  crucible/                                        # Crucible 叠加包
    php/ python/ go/ java/
```

扫描时 ``scan_semgrep``：

```text
--config <本目录>/<lang> --config <本目录>/crucible/<lang>
```

**约束（上游 Fail-Fast）：** ``profile.semgrep_configs`` 每一项必须与本目录下
**文件夹名完全一致**（白名单：`php` / `python` / `go` / `java` / `javascript` /
`typescript`）。画像语言 id（如 `nodejs`）会在派生表里映射为 `javascript`+`typescript`，
禁止把 `nodejs`/`golang` 直接写进 `semgrep_configs`。非法名在
`local_config_names` 丢弃，扫描入口 `require_allowed_lang_dirs` 再拦一道。

## 社区语言目录

本机可把 [semgrep/semgrep-rules](https://github.com/semgrep/semgrep-rules) 的对应语言目录**符号链接或拷贝**到本目录下，例如：

```bash
for lang in php python go java javascript typescript; do
  ln -sfn /path/to/semgrep-rules/$lang backend/semgrep_rules/$lang
done
```

社区树体积大，**不要**把完整 clone 提交进 Git；仓库只跟踪 ``crucible/`` 叠加规则。

## 叠加规则自测

```bash
cd /home/ubuntu/Crucible
mkdir -p .semgrep-xdg/.semgrep
export XDG_CONFIG_HOME=/home/ubuntu/Crucible/.semgrep-xdg
export SEMGREP_LOG_FILE=$XDG_CONFIG_HOME/.semgrep/semgrep.log
export SEMGREP_SETTINGS_FILE=$XDG_CONFIG_HOME/.semgrep/settings.yml
export SEMGREP_VERSION_CACHE_PATH=$XDG_CONFIG_HOME/.semgrep/semgrep_version

.venv/bin/semgrep --disable-version-check --test backend/semgrep_rules/crucible/php/security
.venv/bin/semgrep --disable-version-check --test backend/semgrep_rules/crucible/python
.venv/bin/semgrep --disable-version-check --test backend/semgrep_rules/crucible/go
.venv/bin/semgrep --disable-version-check --test backend/semgrep_rules/crucible/java
```

覆盖矩阵：[`docs/semgrep-coverage-matrix.md`](../../docs/semgrep-coverage-matrix.md)。
