create_clock -name clk -period 10 [get_ports clk]
set_input_delay 1 -clock clk [get_ports {reset enable scan_enable scan_in memory_address*}]
set_output_delay 1 -clock clk [all_outputs]
