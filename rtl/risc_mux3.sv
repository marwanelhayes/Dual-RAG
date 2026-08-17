// =============================================================================
// risc_mux3.sv
// -----------------------------------------------------------------------------
// 4-to-1 Multiplexer for RISC-V processor.
//
// Responsibilities:
//   - Select between four inputs based on 2-bit select signal
//   - Used for FPU operand forwarding selection
// =============================================================================
module risc_mux3
#(
    parameter int DATA_WIDTH = 32
) 
(
    // ─── Input data ───────────────────────────────────────────────────────────
    input [DATA_WIDTH-1:0] IN_1,IN_2,IN_3,IN_4,    // Four input channels
    input [1:0] sel,                               // 2-bit select signal

    // ─── Output ───────────────────────────────────────────────────────────────
    output logic [DATA_WIDTH-1:0] Y                 // Selected output
);

    // ─── 4-to-1 mux implementation ───────────────────────────────────────────
    always_comb 
    begin
        Y = '0;
        case(sel)
            2'b00   : Y = IN_1;
            2'b01   : Y = IN_2;
            2'b10   : Y = IN_3;
            2'b11   : Y = IN_4;
        endcase
    end
endmodule