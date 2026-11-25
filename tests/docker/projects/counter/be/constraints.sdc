create_clock -name clk -period 10 [get_ports clk]
set_input_delay 1 -clock clk [get_ports {reset enable}]
set_output_delay 1 -clock clk [all_outputs]
