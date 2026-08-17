// =============================================================================
// pc_adder.sv
// -----------------------------------------------------------------------------
// PC+4 Adder for RISC-V processor.
//
// Responsibilities:
//   - Calculate next sequential program counter (PC + 4)
//   - Simple combinational adder for pipeline speed
// =============================================================================
module pc_adder
#(
    parameter int ADDR_WIDTH = 32
) 
(
    // ─── Input ─────────────────────────────────────────────────────────────────
    input [ADDR_WIDTH-1:0] PC,    // Current program counter

    // ─── Output ───────────────────────────────────────────────────────────────
    output logic [ADDR_WIDTH-1:0] PCPlus4    // PC + 4 (next sequential)
);

    // ─── PC + 4 calculation ──────────────────────────────────────────────────
    always_comb
    begin
        PCPlus4 = PC + 4;    // Add 4 for next word-aligned address
    end
endmodule