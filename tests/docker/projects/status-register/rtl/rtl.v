module status_register(
  input clk,
  input reset,
  input scan_enable,
  input scan_in,
  input [7:0] data,
  output scan_out,
  output reg [7:0] state
);
`ifndef DEMO_SCAN
  // These ports are used by the DFT representation of the same interface.
  wire unused_scan = &{1'b0, scan_enable, scan_in};
`endif
  wire [7:0] next_state;
  increment inc(.value(data), .next_value(next_state));

  always @(posedge clk) begin
    if (reset)
      state <= 8'b0;
`ifdef DEMO_SCAN
    else if (scan_enable)
      state <= {state[6:0], scan_in};
`endif
    else
      state <= next_state;
  end
  assign scan_out = state[7];
endmodule
