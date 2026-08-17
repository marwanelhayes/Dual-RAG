// =============================================================================
// opcode_decoder.sv
// -----------------------------------------------------------------------------
// Opcode decoder for RISC-V processor.
//
// Responsibilities:
//   - Decode opcode field to generate control signals
//   - Identify instruction type (R, I, S, B, U, J)
//   - Generate ALU sub-operation code
//   - Handle special instructions (CSR, FPU, jumps, branches)
// =============================================================================
import shared_pkg::*;

module opcode_decoder 
#(
    parameter int ALU_SUB_CONTROL_WIDTH = 2
)
(
    // ─── Instruction input ───────────────────────────────────────────────────
    input opcode_t opcode,           // Opcode field
    input csr_t CsrOperation,        // CSR operation type

    // ─── Control signal outputs ───────────────────────────────────────────────
    output logic [ALU_SUB_CONTROL_WIDTH-1:0] ALUControl,  // ALU operation
    output logic Jump,              // Jump instruction flag
    output logic Branch,            // Branch instruction flag
    output logic Immediate,         // Immediate present flag
    output logic MemWrite,          // Memory write enable
    output selector_t Selector,     // Write-back data select
    output logic ALUSrc,            // ALU operand B select
    output logic CsrAccess,         // CSR access enable
    output logic RegWrite,          // Register write enable
    output logic Store,             // Store instruction flag
    output logic MMode,             // Machine mode flag
    output logic IllegaleInstruction,  // Illegal instruction flag
    output logic FPU                // FPU instruction flag
);

    // ─── Main opcode decoding logic ──────────────────────────────────────────
    always_comb
    begin
        // Default values (NOP-like state)
        ALUControl = 2'b11;    // Default to ADD operation
        Jump = 1'b0;
        Branch = 1'b0;
        Immediate = 1'b0;
        MemWrite = 1'b0;
        Selector = ALUToReg;
        ALUSrc = 1'b0;
        RegWrite = 1'b0;
        Store = 1'b0;
        CsrAccess = 1'b0;
        MMode = 1'b0;
        FPU = 1'b0;
        IllegaleInstruction = 1'b1;    // Assume illegal until proven valid

        case(opcode)
            // ── R-type: Register-register operations ─────────────────────────
            R_TYPE: 
            begin
                ALUControl = 2'b10;
                RegWrite = 1'b1;
                Selector = ALUToReg;
                IllegaleInstruction = 1'b0;
            end
            // ── Load: Load from memory ────────────────────────────────────────
            LOAD: 
            begin
                Selector = MemToReg;
                Immediate = 1'b1;
                ALUSrc = 1'b1;
                RegWrite = 1'b1;
                IllegaleInstruction = 1'b0;
            end
            // ── S-type: Store to memory ───────────────────────────────────────
            S_TYPE: 
            begin
                MemWrite = 1'b1;
                ALUSrc = 1'b1;
                Store = 1'b1;
                IllegaleInstruction = 1'b0;
            end
            // ── B-type: Branch operations ─────────────────────────────────────
            B_TYPE: 
            begin
                ALUControl = 2'b00;
                Branch = 1'b1;
                IllegaleInstruction = 1'b0;
            end
            // ── I-type: Immediate operations ──────────────────────────────────
            I_TYPE: 
            begin
                ALUControl = 2'b01;
                Immediate = 1'b1;
                ALUSrc = 1'b1;
                RegWrite = 1'b1;
                IllegaleInstruction = 1'b0;
            end
            // ── JAL: Jump and Link ────────────────────────────────────────────
            JAL: 
            begin
                Jump = 1'b1;
                ALUSrc = 1'b1;
                RegWrite = 1'b1;
                Selector = PCToReg;
                IllegaleInstruction = 1'b0;
            end
            // ── JALR: Jump and Link Register ──────────────────────────────────
            JALR: 
            begin
                Jump = 1'b1;
                ALUSrc = 1'b1;
                Immediate = 1'b1;
                RegWrite = 1'b1;
                Selector = PCToReg;
                IllegaleInstruction = 1'b0;
            end
            // ── CSR: Control and Status Registers ─────────────────────────────
            CSR: 
            begin
                IllegaleInstruction = 1'b0;
                if(CsrOperation == system) 
                begin
                    MMode = 1'b1;    // ECALL, EBREAK, MRET
                end
                else
                begin
                    CsrAccess = 1'b1;
                    RegWrite = 1'b1;
                    Selector = CSRToReg;
                end
            end
            // ── Floating-Point operations ──────────────────────────────────────
            FLOATING_PT:
            begin
                IllegaleInstruction = 1'b0;
                FPU = 1'b1;
            end
        endcase
    end
endmodule
