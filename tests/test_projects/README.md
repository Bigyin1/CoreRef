# Project tree fixture

stdlib -> no dependencies; cpu -> stdlib; soc -> cpu.

These are normal source projects with standard deps.yml documents.
Only stdlib and cpu become Git repositories in tests; soc stays local.
physical uses a custom JSON manifest and packaged files without Git sources.
