source common.tcl
read_lef $platform/lef/NangateOpenCellLibrary.tech.lef
read_lef $platform/lef/NangateOpenCellLibrary.macro.mod.lef
foreach lef [lsort [glob -nocomplain macros/*.lef]] {
  read_lef $lef
}
read_verilog netlist.v
link_design $top
read_sdc constraints.sdc

set die_area {0 0 80 80}
set core_area {5 5 75 75}
set placements $::env(COREREF_MACRO_PLACEMENTS)
if {[llength $placements] > 0} {
  set right 125
  set upper 150
  foreach placement $placements {
    lassign $placement instance x y
    set right [expr {max($right, $x + 100)}]
    set upper [expr {max($upper, $y + 100)}]
  }
  set die_area [list 0 0 $right $upper]
  set core_area [list 5 5 [expr {$right - 5}] [expr {$upper - 5}]]
}

initialize_floorplan -site FreePDK45_38x28_10R_NP_162NW_34O \
  -die_area $die_area -core_area $core_area
source $platform/make_tracks.tcl
foreach placement $placements {
  lassign $placement instance x y
  place_macro -macro_name $instance -location [list $x $y]
}
if {[llength $placements] > 0} {
  cut_rows -halo_width_x 2 -halo_width_y 2
}
place_pins -hor_layers metal5 -ver_layers metal6
source $platform/global_connect.tcl
source $platform/grid_strategy-M1-M4-M7.tcl
if {[llength $macro_cells] > 0} {
  add_pdn_stripe -grid grid -layer metal8 -width 1.6 -pitch 24 -offset 4
  add_pdn_connect -grid grid -layers {metal7 metal8}
  define_pdn_grid -macro -name macro_supply -cells $macro_cells -voltage_domains CORE
  add_pdn_connect -grid macro_supply -layers {metal7 metal8}
}
pdngen
write_db floorplan.odb
