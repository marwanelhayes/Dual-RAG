// =============================================================================
// wb_stage.sv
// -----------------------------------------------------------------------------
// Write-Back stage of the RISC-V 5-stage pipeline.
//
// Responsibilities:
//   - Select correct write-back data based on instruction type
//   - Update program counter with branch/jump target or next sequential
//   - Handle trap entry by redirecting PC to trap vector
//   - Manage pipeline stall from fetch stage
//
// Instantiates:
//   - risc_mux2: PC source selection (sequential vs branch target)
// =============================================================================
import shared_pkg::*;

module wb_stage
#(
    parameter int DATA_WIDTH = 32,
    parameter int ADDR_WIDTH = 32
) 
(
    // ─── Clock and reset ───────────────────────────────────────────────────────
    input clk,
    input rst,

    // ── Branch/jump redirect from EX stage ───────────────────────────────────
    input PCSrcE,                               // Branch/jump taken
    input [ADDR_WIDTH-1:0] PCBranchE,          // Branch target address

    // ── Pipeline control from hazard unit ────────────────────────────────────
    input StallF,                              // Stall fetch stage
    input [ADDR_WIDTH-1:0] PCPlus4F,          // Next sequential PC

    // ── Write-back data inputs ───────────────────────────────────────────────
    input [DATA_WIDTH-1:0] ALUOutW,            // ALU result
    input [DATA_WIDTH-1:0] ReadDataW,         // Loaded data from memory
    input selector_t SelectorW,                // Write-back source select

    // ── CSR and trap interface ────────────────────────────────────────────────
    input logic [ADDR_WIDTH-1:0] CsrOutPC,     // Trap vector address
    input logic TrapIsSet,                    // Trap pending flag
    input [ADDR_WIDTH-1:0] PCPlus4W,          // PC+4 for PC-relative ops
    input [DATA_WIDTH-1:0] CsrOutW,           // CSR read data

    // ── Outputs ───────────────────────────────────────────────────────────────
    output logic [DATA_WIDTH-1:0] ResultW,    // Final write-back result
    output logic [ADDR_WIDTH-1:0] PCF         // PC for fetch stage
);

    // ─── Internal wires – PC selection ────────────────────────────────────────
    wire [ADDR_WIDTH-1:0] PCW;    // Selected PC (sequential or branch)

    // ─── wb_stage: PC source mux ─────────────────────────────────────────────
    risc_mux2 #(.DATA_WIDTH(ADDR_WIDTH)) M2
    (
        .IN_1(PCPlus4F),          // Next sequential address
        .IN_2(PCBranchE),         // Branch/jump target
        .sel(PCSrcE),             // 1 = branch/jump taken
        .Y(PCW)
    );

    // ─── Write-back data selection ───────────────────────────────────────────
    always_comb
    begin
        ResultW = '0;
        case(SelectorW)
            // ── ALU result to register ───────────────────────────────────────
            ALUToReg: ResultW = ALUOutW;
            // ── Memory load to register ──────────────────────────────────────
            MemToReg: ResultW = ReadDataW;
            // ── CSR to register ─────────────────────────────────────────────
            CSRToReg: ResultW = CsrOutW;
            // ── PC+4 to register (JAL) ───────────────────────────────────────
            PCToReg : ResultW = PCPlus4W;
        endcase
    end

    // ─── PC register update ───────────────────────────────────────────────────
    always_ff @(posedge clk or negedge rst) 
    begin
        if(!rst)
        begin
            PCF <= '0;
        end
        else if(TrapIsSet)
        begin
            PCF <= CsrOutPC;           // Jump to trap vector
        end
        else if(!StallF)
        begin
            PCF <= PCW;                // Update PC with selected address
        end
    end
endmodule