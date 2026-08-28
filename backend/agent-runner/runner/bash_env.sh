# Defense-in-depth if BASH_ENV is inherited by a non-interactive Bash child.
# Primary Bash credential strip is PreToolUse env -u (see run_one._bash_command_without_provider_creds).
unset ANTHROPIC_API_KEY
unset ANTHROPIC_AUTH_TOKEN
unset CLAUDE_CODE_OAUTH_TOKEN
unset ANTHROPIC_API_KEY_OLD
unset ANTHROPIC_AUTH_TOKEN_OLD
