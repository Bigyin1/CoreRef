# Git credentials are confined to the disposable GitLab service.
export GIT_TERMINAL_PROMPT=0
export GIT_CONFIG_COUNT=1
export GIT_CONFIG_KEY_0=credential.http://gitlab:18081.helper
export GIT_CONFIG_VALUE_0='!f() {
    if [ "$1" = get ]; then
        printf "username=root\npassword=%s\n" "$COREREF_TEST_PASSWORD"
    fi
}; f'

gitlab=http://gitlab:18081/root

fixture_ref() {
    printf 'git+%s/%s.git?ref=fixture' "$gitlab" "$1"
}

output_path() {
    nix eval --no-update-lock-file --raw "$1.outPath"
}
