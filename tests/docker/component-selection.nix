# Exercise the experimental flow's IP selection separately from stage execution.
{ lib }:
let
  fails = value: !(builtins.tryEval (builtins.deepSeq value true)).success;
  select = import ./flow/nix/select-components.nix { inherit lib; };
  names = map (component: component.name);
  node = name: id: dependencies: { inherit name id dependencies; };
  leaf = node "leaf" "1" [ ];
  mid = node "mid" "1" [ leaf ];
  left = node "left" "1" [ mid ];
  right = node "right" "1" [ mid ];
  conflictingRight = node "right" "1" [
    (node "mid" "1" [ (node "leaf" "2" [ ]) ])
  ];
  selectRtl =
    roots:
    names (select {
      inherit roots;
      viewKey = component: component.views.rtl or null;
    });
  rtlBranch =
    name: files:
    node name "1" [
      (
        leaf
        // {
          views = {
            rtl = files;
            gds = throw "unused physical view";
          };
        }
      )
    ];
  a = rtlBranch "a" [
    "pkg.sv"
    "top.sv"
  ];
  b = rtlBranch "b" [
    "pkg.sv"
    "top.sv"
  ];
  other = rtlBranch "b" [
    "top.sv"
    "pkg.sv"
  ];
  cycle = node "cycle" "1" [ cycle ];
  opaque = node "macro" "1" (throw "opaque macro inputs were inspected");
  cut = component: component.name == "macro";
in
assert
  names (select {
    roots = [ left ];
    filter = component: component.name != "mid";
  }) == [
    "leaf"
    "left"
  ];
assert
  names (select {
    roots = [
      left
      right
    ];
  }) == [
    "leaf"
    "mid"
    "left"
    "right"
  ];
assert fails (select {
  roots = [
    left
    conflictingRight
  ];
});
assert
  selectRtl [
    a
    b
  ] == [
    "leaf"
    "a"
    "b"
  ];
assert
  selectRtl [
    b
    a
  ] == [
    "leaf"
    "b"
    "a"
  ];
assert fails (selectRtl [
  a
  other
]);
assert fails (selectRtl [
  other
  a
]);
assert fails (select {
  roots = [ cycle ];
});
assert
  names (select {
    roots = [ opaque ];
    stopAt = cut;
  }) == [ "macro" ];
assert builtins.elem "leaf" (
  names (select {
    roots = [
      opaque
      left
    ];
    stopAt = cut;
  })
);
true
