module soc(
  input clk,
  input reset,
  input enable,
  input scan_enable,
  input scan_in,
  input [2:0] memory_address,
  output scan_out,
  output [7:0] count,
  output [7:0] scanned_state,
  output [3:0] cache_data,
  output [7:0] scratch_data
);
  counter hard_macro(.clk(clk), .reset(reset), .enable(enable), .value(count));
  status_register status(
    .clk(clk), .reset(reset), .scan_enable(scan_enable), .scan_in(scan_in),
    .data(count), .scan_out(scan_out), .state(scanned_state)
  );
  sram_4x4 cache(
    .clk(clk), .write_enable(enable), .address(memory_address[1:0]),
    .write_data(count[3:0]), .read_data(cache_data)
  );
  sram_8x8 scratch(
    .clk(clk), .write_enable(enable), .address(memory_address),
    .write_data(scanned_state), .read_data(scratch_data)
  );
endmodule
