set platform $::env(COREREF_PLATFORM)
set top $::env(COREREF_TOP)
set macro_cells $::env(COREREF_MACRO_CELLS)
read_liberty $platform/lib/NangateOpenCellLibrary_typical.lib
foreach liberty [lsort [glob -nocomplain macros/*.lib]] {
  read_liberty $liberty
}
