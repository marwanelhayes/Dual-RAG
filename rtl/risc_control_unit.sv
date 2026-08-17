// =============================================================================
// risc_control_unit.sv
// -----------------------------------------------------------------------------
// Control unit for RISC-V processor decode stage.
//
// Responsibilities:
//   - Decode opcode to generate high-level control signals
//   - Decode funct3/funct7 fields to generate ALU operation
//   - Generate FPU operation and move control signals
//   - Combine integer and FPU register write enables
//   - Detect illegal instructions
//
// Instantiates:
//   - opcode_decoder: main opcode decoding
//   - alu_decoder: ALU function decoding
//   - fpu_decoder: FPU instruction decoding
// =============================================================================
import shared_pkg::*;   

module risc_control_unit 
#(
    parameter int ALU_SUB_CONTROL_WIDTH = 2 
)
(
    // ─── Instruction fields ───────────────────────────────────────────────────
    input opcode_t opcode,           // Opcode field (bits 6:0)
    input [2:0] funct3,             // Function field 3 (bits 14:12)
    input [6:0] funct7,            // Function field 7 (bits 31:25)
    input csr_t CsrOperationD,      // CSR operation type
    input gpr_t Rs2,               // Source register 2 index

    // ─── Control signal outputs ───────────────────────────────────────────────
    output alu_operation_t ALUControlD,  // ALU operation select
    output logic RegWriteD,              // Combined register write enable
    output selector_t SelectorD,        // Write-back data select
    output MemWriteD,                   // Memory write enable
    output BranchD,                     // Branch instruction flag
    output ImmediateD,                  // Immediate present flag
    output ALUSrcD,                     // ALU operand B select
    output StoreD,                      // Store instruction flag
    output CsrAccessD,                  // CSR access enable
    output MModeD,                      // Machine mode enable
    output JumpD,                       // Jump instruction flag
    output IllegaleInstructionD,        // Illegal instruction flag

    // ─── FPU control outputs ──────────────────────────────────────────────────
    output fpu_operation_t FPUControlD,   // FPU operation type
    output move_operation_t MoveOperationD, // FPU move operation
    output FPURegWriteD,                  // FPR write enable
    output FPUValidD                     // Valid FPU operation
);

    // ─── Internal wires – control signals ─────────────────────────────────────
    wire [ALU_SUB_CONTROL_WIDTH-1:0] ALUControl;    // ALU control from decoder
    logic WriteFromFPU, RegWrite;                  // FPU and integer write enables
    wire FPUD;                                     // FPU instruction flag

    // ─── Combined register write enable ───────────────────────────────────────
    always_comb
    begin
        RegWriteD = RegWrite | WriteFromFPU;    // OR integer and FPU write enables
    end

    // ─── risc_control_unit: opcode decoder ────────────────────────────────────
    opcode_decoder #(.ALU_SUB_CONTROL_WIDTH(ALU_SUB_CONTROL_WIDTH)) opdec(
        .opcode(opcode),
        .ALUControl(ALUControl),
        .Jump(JumpD),
        .Branch(BranchD),
        .Immediate(ImmediateD),
        .CsrOperation(CsrOperationD),
        .MemWrite(MemWriteD),
        .Selector(SelectorD),
        .ALUSrc(ALUSrcD),
        .Store(StoreD),
        .RegWrite(RegWrite),
        .CsrAccess(CsrAccessD),
        .MMode(MModeD),
        .FPU(FPUD),
        .IllegaleInstruction(IllegaleInstructionD)
    );
    

    alu_decoder #(.ALU_SUB_CONTROL_WIDTH(ALU_SUB_CONTROL_WIDTH)) aludec(
        .funct3(funct3),
        .funct7(funct7),
        .ALUControl(ALUControl),
        .ALUControlD(ALUControlD)
    );

    fpu_decoder fpudec(
        .funct3(funct3),
        .funct7(funct7),
        .Rs2(Rs2),
        .FPUD(FPUD), 
        .FPUControlD(FPUControlD),
        .MoveOperationD(MoveOperationD),
        .FPURegWriteD(FPURegWriteD),
        .Write(WriteFromFPU),
        .FPUValidD(FPUValidD)
    );


endmodule