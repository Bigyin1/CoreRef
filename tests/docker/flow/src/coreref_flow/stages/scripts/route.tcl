source common.tcl
read_db cts.odb
read_sdc constraints.sdc
source $platform/setRC.tcl
set_propagated_clock [all_clocks]

set_routing_layers -signal metal2-metal10 -clock metal4-metal10
global_route
detailed_route -output_drc routing_drc.rpt
if {![design_is_routed]} {
  error "The design is not fully routed"
}
filler_placement {FILLCELL_X1 FILLCELL_X2 FILLCELL_X4 FILLCELL_X8 FILLCELL_X16 FILLCELL_X32}
global_connect

extract_parasitics -ext_model_file $platform/rcx_patterns.rules
write_spef parasitics.spef
read_spef parasitics.spef
report_checks -path_delay min_max > timing.rpt
set_clock_latency -source 0 [all_clocks]
write_timing_model timing.lib
write_abstract_lef -bloat_occupied_layers abstract.lef
write_db routed.odb
write_def final.def
write_verilog routed.v
