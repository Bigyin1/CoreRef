source common.tcl
read_db floorplan.odb
read_sdc constraints.sdc
source $platform/setRC.tcl
global_placement -density 0.35
detailed_placement
check_placement
write_db placed.odb
