// =============================================================================
// hazard_unit.sv
// -----------------------------------------------------------------------------
// Hazard detection and forwarding unit for RISC-V 5-stage pipeline.
//
// Responsibilities:
//   - Detect load-use hazards (LW stall)
//   - Detect FPU busy hazards
//   - Generate forwarding multiplexor selects for GPR operands
//   - Generate forwarding selects for FPR operands
//   - Generate pipeline stall signals
//   - Generate pipeline flush signals for branches and traps
// =============================================================================
import shared_pkg::*;

module hazard_unit 
(
    // ─── Execute stage register indices ───────────────────────────────────────
    input   gpr_t Rs1E,               // Source register 1 (execute)
    input   gpr_t Rs2E,               // Source register 2 (execute)
    input   gpr_t RdE,                // Destination register (execute)

    // ─── Decode stage register indices ────────────────────────────────────────
    input   gpr_t Rs1D,               // Source register 1 (decode)
    input   gpr_t Rs2D,               // Source register 2 (decode)

    // ─── Memory/Write-back register indices ───────────────────────────────────
    input   gpr_t RdM,                // Destination register (memory)
    input   gpr_t RdW,                // Destination register (writeback)

    // ─── Register write enables ───────────────────────────────────────────────
    input   logic RegWriteM,          // GPR write enable (memory)
    input   logic RegWriteW,          // GPR write enable (writeback)

    // ─── Control signals ───────────────────────────────────────────────────────
    input   selector_t SelectorE,     // Write-back select (execute)
    input   logic PCSrcE,            // Branch/jump taken
    input   logic TrapIsSet,         // Trap pending

    // ─── FPU move operation ───────────────────────────────────────────────────
    input   move_operation_t MoveOperationE,  // FPU move type

    // ─── FPR indices ───────────────────────────────────────────────────────────
    input   fpr_t RdFM,              // FPR destination (memory)
    input   fpr_t RdFW,              // FPR destination (writeback)
    input   fpr_t Rs1FE,             // FPR source 1 (execute)
    input   fpr_t Rs2FE,             // FPR source 2 (execute)

    // ─── FPR write enables ───────────────────────────────────────────────────
    input   logic FPURegWriteM,      // FPR write enable (memory)
    input   logic FPURegWriteW,      // FPR write enable (writeback)

    // ─── FPU status ───────────────────────────────────────────────────────────
    input   logic FPUValidE,         // Valid FPU operation
    input   logic FPUBusyM,          // FPU busy flag

    // ─── Cache status ───────────────────────────────────────────────────────────
    input   logic ICacheHit,         // Instruction cache hit
    input   logic DCacheHit,         // Data cache hit

    // ─── Forwarding outputs ───────────────────────────────────────────────────
    output  logic [2:0] ForwardAE,    // GPR operand A forward select
    output  logic [2:0] ForwardBE,    // GPR operand B forward select
    output  logic [1:0] ForwardFloatingAE,  // FPR operand A forward select
    output  logic [1:0] ForwardFloatingBE,  // FPR operand B forward select

    // ─── Pipeline control outputs ─────────────────────────────────────────────
    output  logic StallD,            // Stall decode stage
    output  logic StallF,            // Stall fetch stage
    output  logic FlushE,           // Flush execute stage
    output  logic FlushD            // Flush decode stage
);

    // ─── Internal registers – hazard detection flags ─────────────────────────
    logic LWStall,FPUStall;

    // ─── Load-Use hazard detection ───────────────────────────────────────────
    always_comb 
    begin
        LWStall = 1'b0;
        if (SelectorE == MemToReg && ((RdE == Rs1D) || (RdE == Rs2D))) 
        begin
            LWStall = 1'b1;    // Load word result not yet available
        end
    end

    // ─── FPU busy hazard detection ───────────────────────────────────────────
    always_comb
    begin
        FPUStall = 1'b0;
        if (FPUValidE && FPUBusyM)
        begin
            FPUStall = 1'b1;    // FPU still processing previous operation
        end
    end

    // ─── GPR operand A forwarding ────────────────────────────────────────────
    always_comb
    begin
        ForwardAE = 3'b000; 
        if (RegWriteW && (RdW != zero) && (RdW == Rs1E) && (MoveOperationE != FPUToReg)) 
        begin
            ForwardAE = 3'b001;    // Forward from WB stage
        end
        else if (RegWriteM && (RdM != zero) && (RdM == Rs1E) && (MoveOperationE != FPUToReg)) 
        begin
            ForwardAE = 3'b010;    // Forward from MEM stage
        end
        else if(RegWriteW && (RdW != zero) && (RdW == Rs1E) && (MoveOperationE == FPUToReg))
        begin
            ForwardAE = 3'b011;    // Forward FPU result from WB
        end
        else if (RegWriteM && (RdM != zero) && (RdM == Rs1E) && (MoveOperationE == FPUToReg)) 
        begin
            ForwardAE = 3'b100;    // Forward FPU result from MEM
        end 
    end

    // ─── GPR operand B forwarding ────────────────────────────────────────────
    always_comb
    begin
        ForwardBE = 3'b000;
        if (RegWriteW && (RdW != zero) && (RdW == Rs2E) && (MoveOperationE != FPUToReg)) 
        begin
            ForwardBE = 3'b001;
        end
        else if (RegWriteM && (RdM != zero) && (RdM == Rs2E) && (MoveOperationE != FPUToReg)) 
        begin
            ForwardBE = 3'b010;
        end
        else if(RegWriteW && (RdW != zero) && (RdW == Rs2E) && (MoveOperationE == FPUToReg))
        begin
            ForwardBE = 3'b011;
        end
        else if (RegWriteM && (RdM != zero) && (RdM == Rs2E) && (MoveOperationE == FPUToReg)) 
        begin
            ForwardBE = 3'b100;
        end  
    end

    // ─── FPR operand A forwarding ────────────────────────────────────────────
    always_comb
    begin
        ForwardFloatingAE = 2'b00;
        if (FPURegWriteW && (RdFW != f0) && (RdFW == Rs1FE)) 
        begin
            ForwardFloatingAE = 2'b01;
        end
        else if (FPURegWriteM && (RdFM != f0) && (RdFM == Rs1FE)) 
        begin
            ForwardFloatingAE = 2'b10;
        end 
    end

    // ─── FPR operand B forwarding ────────────────────────────────────────────
    always_comb
    begin
        ForwardFloatingBE = 2'b00;
        if (FPURegWriteW && (RdFW != f0) && (RdFW == Rs2FE)) 
        begin
            ForwardFloatingBE = 2'b01;
        end
        else if (FPURegWriteM && (RdFM != f0) && (RdFM == Rs2FE)) 
        begin
            ForwardFloatingBE = 2'b10;
        end 
    end
        
    // ─── Pipeline stall and flush generation ────────────────────────────────
    always_comb
    begin
        StallD = LWStall || FPUStall || (!ICacheHit) || (!DCacheHit);
        StallF = LWStall || FPUStall || (!ICacheHit) || (!DCacheHit);
        FlushD = PCSrcE  || TrapIsSet;
        FlushE = LWStall || PCSrcE || TrapIsSet;
    end
endmodule