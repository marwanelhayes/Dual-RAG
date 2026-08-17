// =============================================================================
// fp_reg_file.sv
// -----------------------------------------------------------------------------
// Floating-Point Register (FPR) File for RISC-V processor.
//
// Responsibilities:
//   - Provide dual-ported read access for FPU operands
//   - Provide single-ported write access for FPU results
//   - Store 32 floating-point registers (f0-f31)
//   - f0 is hardwired to zero
// =============================================================================
import shared_pkg::*;

module fp_reg_file
#(
    parameter int DATA_WIDTH = 32,
    parameter int ADDR_WIDTH = 5
)
(
    // ─── Clock and reset ───────────────────────────────────────────────────────
    input clk,
    input rst,

    // ─── Read/Write interface ─────────────────────────────────────────────────
    input fpr_t Rs1,             // Source register 1 index
    input fpr_t Rs2,             // Source register 2 index
    input fpr_t Rd,              // Destination register index
    input [DATA_WIDTH-1:0] WData,    // Write data
    input WE,                    // Write enable

    // ─── Read data outputs ───────────────────────────────────────────────────
    output logic [DATA_WIDTH-1:0] RD1,   // Read data port 1
    output logic [DATA_WIDTH-1:0] RD2    // Read data port 2
);

    localparam int DEPTH = 2**ADDR_WIDTH;
    
    // ─── Internal registers – register file storage ─────────────────────────
    logic [DATA_WIDTH-1:0] mem [32];    // 32 × 32-bit registers

    // ─── Combinational read ─────────────────────────────────────────────────
    assign RD1 = mem[Rs1];    // Read source 1
    assign RD2 = mem[Rs2];    // Read source 2

    // ─── Synchronous write on negative edge ─────────────────────────────────
    always_ff @(negedge clk or negedge rst) 
    begin
        if (!rst) 
        begin
            foreach(mem[i]) 
                mem[i] <= '0;    // Reset all registers
        end
        else if (WE) 
        begin
            mem[Rd] <= WData;    // Write to destination register
        end
    end
endmodule:fp_reg_file