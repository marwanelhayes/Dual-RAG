module mod_a (
    input  logic clk,
    input  logic rst,
    output logic done
);
    logic counter;

    always_ff @(posedge clk)
    begin
        counter <= counter + 1;
        done    <= counter;
    end
endmodule
