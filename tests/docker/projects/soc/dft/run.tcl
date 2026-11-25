# Keep the SoC module and use the scan implementations of its dependencies.
set output [open [lindex $argv 0] w]
foreach source [lrange $argv 1 end] {
    set rtl [open $source r]
    fcopy $rtl $output
    puts $output ""
    close $rtl
}
close $output
