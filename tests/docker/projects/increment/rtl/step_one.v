module increment_step(input [7:0] value, output [7:0] next_value);
`ifdef INCREMENT_ONE
  assign next_value = value + 8'd1;
`else
  assign next_value = value + 8'd3;
`endif
endmodule
