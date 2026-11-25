# Put the test functions on the same Python import path as the runner.
{ pkgs, core }:
pkgs.python3.withPackages (p: [
  (core.pythonPackage p)
  (p.toPythonModule (
    pkgs.runCommand "coreref-test-stages" { } ''
      mkdir -p "$out/${p.python.sitePackages}"
      cp ${./steps.py} "$out/${p.python.sitePackages}/coreref_test_stages.py"
    ''
  ))
])
