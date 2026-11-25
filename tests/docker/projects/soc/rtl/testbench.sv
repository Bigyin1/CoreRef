module testbench;
  localparam int STEP = `TB_COUNTER_STEP;
  logic clk, reset, enable, scan_enable, scan_in, scan_out;
  logic [2:0] memory_address;
  logic [7:0] count, scanned_state, scratch_data;
  logic [3:0] cache_data;
  soc dut(
    .clk(clk), .reset(reset), .enable(enable), .scan_enable(scan_enable), .scan_in(scan_in),
    .memory_address(memory_address), .scan_out(scan_out), .count(count),
    .scanned_state(scanned_state), .cache_data(cache_data), .scratch_data(scratch_data)
  );

  task automatic tick;
    #1 clk = 1;
    #1 clk = 0;
  endtask

  initial begin
    clk = 0;
    reset = 1;
    enable = 0;
    scan_enable = 0;
    scan_in = 0;
    memory_address = 1;
    tick();
    assert (count == 0 && scanned_state == 0) else $fatal(1, "SoC reset");

    reset = 0;
    enable = 1;
    repeat (2) tick();
    assert (count == 8'(2 * STEP) && scanned_state == 8'(STEP + 1)) else $fatal(1, "counter to status datapath");
    enable = 0;
    tick();
    assert (count == 8'(2 * STEP) && scanned_state == 8'(2 * STEP + 1)) else $fatal(1, "counter hold and status capture");
    assert (cache_data == 4'(STEP) && scratch_data == 1) else $fatal(1, "SRAM read at address 1");

    memory_address = 6;
    enable = 1;
    tick();
    enable = 0;
    tick();
    assert (cache_data == 4'(2 * STEP) && scratch_data == 8'(2 * STEP + 1)) else $fatal(1, "independent SRAM data");
    memory_address = 1;
    tick();
    assert (cache_data == 4'(STEP) && scratch_data == 1) else $fatal(1, "separate SRAM addresses");

    scan_enable = 1;
    scan_in = 1;
    tick();
`ifdef COREREF_DFT
    assert (scanned_state == 8'(2 * (3 * STEP + 1) + 1) && !scan_out) else $fatal(1, "hierarchical scan shift");
`else
    assert (scanned_state == 8'(3 * STEP + 1) && !scan_out) else $fatal(1, "hierarchical RTL capture");
`endif
    $display("PASS SoC");
    $finish;
  end
endmodule
