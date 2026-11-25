module testbench;
  logic [7:0] value, next_value;
  increment dut(.value(value), .next_value(next_value));

  initial begin
    for (int i = 0; i < 256; i++) begin
      value = 8'(i);
      #1;
      assert (next_value == 8'(i + 1))
        else $fatal(1, "increment %0d: expected %0d, got %0d", value, 8'(i + 1), next_value);
    end
    $display("PASS increment");
    $finish;
  end
endmodule
