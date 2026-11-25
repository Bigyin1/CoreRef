module testbench;
  localparam int Width = `TB_WIDTH;
  localparam int Depth = `TB_DEPTH;
  localparam int AddrWidth = $clog2(Depth);
  logic clk, write_enable;
  logic [AddrWidth-1:0] address;
  logic [Width-1:0] write_data, read_data;
  `TB_SRAM dut(
    .clk(clk), .write_enable(write_enable), .address(address),
    .write_data(write_data), .read_data(read_data)
  );

  task automatic tick;
    #1 clk = 1;
    #1 clk = 0;
  endtask

  initial begin
    clk = 0;
    write_enable = 1;
    address = 0;
    write_data = Width'('ha5);
    tick();

    address = AddrWidth'(Depth - 1);
    write_data = Width'('h5a);
    tick();

    write_enable = 0;
    tick();
    assert (read_data == Width'('h5a)) else $fatal(1, "read last address");
    address = 0;
    tick();
    assert (read_data == Width'('ha5)) else $fatal(1, "read first address");

    write_enable = 1;
    write_data = '1;
    tick();
    write_enable = 0;
    tick();
    assert (read_data == '1) else $fatal(1, "overwrite first address");
    $display("PASS SRAM %0dx%0d", Depth, Width);
    $finish;
  end
endmodule
