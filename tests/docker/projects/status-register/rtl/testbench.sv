module testbench;
  logic clk, reset, scan_enable, scan_in, scan_out;
  logic [7:0] data, state;
  status_register dut(
    .clk(clk), .reset(reset), .scan_enable(scan_enable), .scan_in(scan_in), .data(data),
    .scan_out(scan_out), .state(state)
  );

  task automatic tick;
    #1 clk = 1;
    #1 clk = 0;
  endtask

  initial begin
    clk = 0;
    reset = 1;
    scan_enable = 0;
    scan_in = 0;
    data = 0;
    tick();
    assert (state == 0 && !scan_out) else $fatal(1, "status register reset");

    reset = 0;
    data = 8'h7f;
    tick();
    assert (state == 8'h80 && scan_out) else $fatal(1, "status register functional capture");

    scan_enable = 1;
    scan_in = 1;
    data = 8'h7e;
    tick();
`ifdef COREREF_DFT
    assert (state == 8'h01 && !scan_out) else $fatal(1, "status register scan shift");
`else
    assert (state == 8'h7f && !scan_out) else $fatal(1, "status register RTL capture");
`endif
    $display("PASS status-register");
    $finish;
  end
endmodule
