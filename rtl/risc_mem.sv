// =============================================================================
// risc_mem.sv
// -----------------------------------------------------------------------------
// Single-port memory cell for register file implementation.
//
// Responsibilities:
//   - Single port read/write memory for register file byte lane
//   - Provide two simultaneous read ports (Rs1, Rs2)
//   - Provide one write port (Rd) on negative clock edge
//   - Reset all entries to zero on asynchronous reset
//
// Note: Used to implement register file with 4 byte-lane instances
// =============================================================================
import shared_pkg::*;

module risc_mem
#(
    parameter int DATA_WIDTH = 8,    // 8 bits per byte lane
    parameter int ADDR_WIDTH = 5     // 32 registers
) 
(
    // ─── Clock and reset ───────────────────────────────────────────────────────
    input clk,
    input rst,

    // ─── Register file interface ────────────────────────────────────────────
    input gpr_t Rs1,                        // Read address 1
    input gpr_t Rs2,                        // Read address 2
    input gpr_t Rd,                         // Write address
    input signed [DATA_WIDTH-1:0] WData,   // Write data
    input WE,                               // Write enable

    // ─── Read data outputs ───────────────────────────────────────────────────
    output logic signed [DATA_WIDTH-1:0] RD1,    // Read data port 1
    output logic signed [DATA_WIDTH-1:0] RD2     // Read data port 2
);
    // ─── Internal parameters ─────────────────────────────────────────────────
    localparam int DEPTH = 2**ADDR_WIDTH;    // 32 registers
    
    // ─── Internal registers – memory storage ────────────────────────────────
    logic [DATA_WIDTH-1:0] mem [32];    // 32 × 8-bit registers

    // ─── Combinational read ─────────────────────────────────────────────────
    assign RD1 = mem[Rs1];    // Read from source register 1
    assign RD2 = mem[Rs2];    // Read from source register 2

    // ─── Synchronous write on negative clock edge ──────────────────────────
    always_ff @(negedge clk or negedge rst) 
    begin
        if (!rst) 
        begin
            foreach(mem[i]) 
                mem[i] <= '0;    // Reset all registers to zero
        end
        else if (WE) 
        begin
            mem[Rd] <= WData;    // Write to destination register
        end
    end

endmodule