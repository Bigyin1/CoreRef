set output [open [lindex $argv 0] w]
puts $output {`define DEMO_SCAN}
foreach source [lrange $argv 1 end] {
    set rtl [open $source r]
    fcopy $rtl $output
    puts $output ""
    close $rtl
}
puts $output {`undef DEMO_SCAN}
close $output
