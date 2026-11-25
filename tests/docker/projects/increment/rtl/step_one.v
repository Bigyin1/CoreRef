module increment_step(input [7:0] value, output [7:0] next_value);
  assign next_value = value + 8'd1;
endmodule
