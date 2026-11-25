module increment(input [7:0] value, output [7:0] next_value);
  increment_step step(.value(value), .next_value(next_value));
endmodule
