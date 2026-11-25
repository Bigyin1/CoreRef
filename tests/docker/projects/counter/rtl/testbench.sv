module testbench;
  localparam int STEP = `TB_COUNTER_STEP;
  logic clk, reset, enable;
  logic [7:0] value;
  counter dut(.clk(clk), .reset(reset), .enable(enable), .value(value));

  task automatic tick;
    #1 clk = 1;
    #1 clk = 0;
  endtask

  initial begin
    clk = 0;
    reset = 1;
    enable = 1;
    tick();
    assert (value == 0) else $fatal(1, "counter reset");

    reset = 0;
    repeat (2) tick();
    assert (value == 8'(2 * STEP)) else $fatal(1, "counter increment");

    enable = 0;
    repeat (3) tick();
    assert (value == 8'(2 * STEP)) else $fatal(1, "counter hold");

    enable = 1;
    repeat (254) tick();
    assert (value == 0) else $fatal(1, "counter wraparound");
    $display("PASS counter");
    $finish;
  end
endmodule
