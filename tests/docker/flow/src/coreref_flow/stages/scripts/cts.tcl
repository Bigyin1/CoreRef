source common.tcl
read_db placed.odb
read_sdc constraints.sdc
source $platform/setRC.tcl
clock_tree_synthesis
detailed_placement
check_placement
write_db cts.odb
