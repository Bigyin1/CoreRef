set platform $::env(COREREF_PLATFORM)
set top $::env(COREREF_TOP)
set macro_cell $::env(COREREF_MACRO)
set macro_instance {}
read_liberty $platform/lib/NangateOpenCellLibrary_typical.lib
if {$macro_cell ne ""} {
  set macro_instance $::env(COREREF_MACRO_INSTANCE)
  read_liberty macro.lib
}
