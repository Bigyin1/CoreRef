module counter(
  input clk,
  input reset,
  input enable,
  output reg [7:0] value
);
  wire [7:0] next_value;
  increment increment_value(.value(value), .next_value(next_value));

  always @(posedge clk)
    if (reset)
      value <= 8'd0;
    else if (enable)
      value <= next_value;
endmodule
