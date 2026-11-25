# Initialize only the noninteractive module loader used by the stage.
export MODULEPATH=/etc/coreref/eda/test-v1/modulefiles
export MODULES_SET_SHELL_STARTUP=0
module() {
    local module_shell
    module_shell=$(/usr/bin/modulecmd bash "$@") || return
    eval "$module_shell"
}
