// =============================================================================
// risc_mux2.sv
// -----------------------------------------------------------------------------
// 2-to-1 Multiplexer for RISC-V processor.
//
// Responsibilities:
//   - Select between two inputs based on single-bit select signal
//   - Used for operand selection (immediate vs register)
// =============================================================================
module risc_mux2
#(
    parameter int DATA_WIDTH = 32
) 
(
    // ─── Input data ───────────────────────────────────────────────────────────
    input [DATA_WIDTH-1:0] IN_1,IN_2,    // Two input channels
    input sel,                           // Select signal

    // ─── Output ───────────────────────────────────────────────────────────────
    output logic [DATA_WIDTH-1:0] Y       // Selected output
);

    // ─── 2-to-1 mux implementation ──────────────────────────────────────────
    always_comb 
    begin
        if(sel)
        begin
            Y = IN_2;    // Select second input
        end
        else
        begin
            Y = IN_1;    // Select first input
        end
    end
endmodule