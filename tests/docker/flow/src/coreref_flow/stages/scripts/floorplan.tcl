source common.tcl
read_lef $platform/lef/NangateOpenCellLibrary.tech.lef
read_lef $platform/lef/NangateOpenCellLibrary.macro.mod.lef
if {$macro_instance ne ""} {
  read_lef macro.lef
}
read_verilog netlist.v
link_design $top
read_sdc constraints.sdc

set die_area {0 0 80 80}
set core_area {5 5 75 75}
if {$macro_instance ne ""} {
  set die_area {0 0 125 150}
  set core_area {5 5 120 145}
}

initialize_floorplan -site FreePDK45_38x28_10R_NP_162NW_34O \
  -die_area $die_area -core_area $core_area
source $platform/make_tracks.tcl
if {$macro_instance ne ""} {
  place_macro -macro_name $macro_instance -location {21 47}
  cut_rows -halo_width_x 2 -halo_width_y 2
}
place_pins -hor_layers metal5 -ver_layers metal6
source $platform/global_connect.tcl
source $platform/grid_strategy-M1-M4-M7.tcl
if {$macro_instance ne ""} {
  add_pdn_stripe -grid grid -layer metal8 -width 1.6 -pitch 24 -offset 4
  add_pdn_connect -grid grid -layers {metal7 metal8}
  define_pdn_grid -macro -name counter_supply -cells $macro_cell -voltage_domains CORE
  add_pdn_connect -grid counter_supply -layers {metal7 metal8}
}
pdngen
write_db floorplan.odb
