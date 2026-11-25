set rtl [open [lindex $argv 0] r]
set output [open [lindex $argv 1] w]
puts $output {`define DEMO_SCAN}
fcopy $rtl $output
puts $output "\n`undef DEMO_SCAN"
close $rtl
close $output
