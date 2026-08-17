// =============================================================================
// execute_stage.sv
// -----------------------------------------------------------------------------
// Execute stage of the RISC-V 5-stage pipeline.
//
// Responsibilities:
//   - Execute ALU operations (arithmetic, logical, shift, compare)
//   - Execute floating-point operations via FPU
//   - Handle branch evaluation and jump targets
//   - Perform data forwarding from later pipeline stages
//   - Manage CSR read/write operations and trap detection
//   - Compute memory addresses for loads/stores
//
// Instantiates:
//   - risc_alu: integer ALU
//   - risc_fpu: floating-point unit
//   - risc_mux2, risc_mux3, risc_mux4: operand selection muxes
//   - csr_file: control and status register file
// =============================================================================
import shared_pkg::*;
`define VERIF 1

module execute_stage
#(
    parameter int DATA_WIDTH = 32,
    parameter int ADDR_WIDTH = 32,
    parameter int ALU_SUB_CONTROL_WIDTH = 2,
    parameter int STAGES = 4
)
(
    // ─── Clock and reset ───────────────────────────────────────────────────────
    input clk,
    input rst,

    // ── EX/MEM register inputs ────────────────────────────────────────────────
    input signed [DATA_WIDTH-1:0] RD1E,        // GPR rs1 value
    input signed [DATA_WIDTH-1:0] RD2E,        // GPR rs2 value
    input signed [DATA_WIDTH-1:0] SignImmE,    // Sign-extended immediate
    input [ADDR_WIDTH-1:0] PCPlus4E,           // PC+4 from decode
    input alu_operation_t ALUControlE,         // ALU operation select
    input logic [2:0] funct3E,                 // funct3 field
    input BranchE,                             // Branch instruction flag
    input JumpE,                               // Jump instruction flag

    // ── Forwarding inputs ─────────────────────────────────────────────────────
    input [2:0] ForwardAE,                     // Forward select for operand A
    input [2:0] ForwardBE,                     // Forward select for operand B
    input signed [DATA_WIDTH-1:0] ResultW,     // Write-back result

    // ── Register file interface ───────────────────────────────────────────────
    input gpr_t Rs1E,                          // Source register 1 index
    input gpr_t RdE,                           // Destination register index
    input RegWriteE,                          // Register write enable

    // ── CSR interface ─────────────────────────────────────────────────────────
    input CsrAccessE,                          // CSR access enable
    input csr_t CsrOperationE,                 // CSR operation type
    input csr_index_t CsrIndexE,              // CSR register address
    input TimerInterrupt,                     // Timer interrupt pending
    input ExternalInterrupt,                   // External interrupt pending
    input SoftwareInterrupt,                  // Software interrupt pending

    // ── Control signals ───────────────────────────────────────────────────────
    input selector_t SelectorE,                // Write-back data select
    input ALUSrcE,                            // ALU operand B select
    input MemWriteE,                          // Memory write enable

    // ── Special instructions ─────────────────────────────────────────────────
    input MRetE,                               // MRET instruction flag
    input EcallE,                              // ECALL instruction flag
    input EbreakE,                             // EBREAK instruction flag
    input IllegaleInstructionE,                // Illegal instruction flag

    // ── FPR inputs from decode stage ─────────────────────────────────────────
    input fpr_t RdFE,                          // FPR destination index
    input [DATA_WIDTH-1:0] RD1FE,             // FPR rs1 value
    input [DATA_WIDTH-1:0] RD2FE,             // FPR rs2 value
    input fpu_operation_t FPUControlE,         // FPU operation type
    input round_mode_t RoundModeE,             // Rounding mode
    input FPURegWriteE,                        // FPR write enable
    input FPUValidE,                          // Valid FPU operation
    input move_operation_t MoveOperationE,    // FPU move operation type
    input fpr_t Rs1FE,                         // FPR source register 1 index
    input fpr_t Rs2FE,                         // FPR source register 2 index

    // ── FPU forwarding inputs ────────────────────────────────────────────────
    input logic [DATA_WIDTH-1:0] FPUOutW,      // FPU write-back result
    input logic [1:0] ForwardFloatingAE,      // FPU forward select A
    input logic [1:0] ForwardFloatingBE,      // FPU forward select B

    // ── Outputs to memory stage ─────────────────────────────────────────────
    output logic signed [DATA_WIDTH-1:0] ALUOutM,      // ALU result
    output logic signed [DATA_WIDTH-1:0] WriteDataM,   // Data to store
    output gpr_t RdM,                                 // Destination register
    output logic PCSrcE,                              // Branch/jump taken
    output logic RegWriteM,                          // Register write enable
    output logic [ADDR_WIDTH-1:0] PCPlus4M,          // PC+4 to next stage
    output selector_t SelectorM,                     // Write-back select
    output logic [2:0] funct3M,                      // funct3 to memory
    output logic [DATA_WIDTH-1:0] CsrOutM,           // CSR read data
    output logic MemWriteM,                          // Memory write enable

    // ── Trap and CSR outputs ────────────────────────────────────────────────
    output logic TrapIsSet,                         // Trap pending flag
    output logic [ADDR_WIDTH-1:0] CsrOutPC,          // Trap vector address

    // ── FPU outputs to memory stage ─────────────────────────────────────────
    output fpr_t RdFM,                              // FPR destination
    output logic OverflowM,                         // Floating-point overflow
    output logic UnderflowM,                        // Floating-point underflow
    output logic NaNM,                              // Not-a-Number flag
    output logic InfM,                              // Infinity flag
    output logic ZeroM,                            // Zero result flag
    output logic InvalidDivM,                       // Invalid division flag
    output logic [DATA_WIDTH-1:0] FPUOutM,          // FPU result
    output logic FPURegWriteM,                     // FPR write enable
    output move_operation_t MoveOperationM,         // FPU move operation
    output logic FPUBusyM,                          // FPU busy flag
    output logic FPUDoneM                           // FPU done flag
);

    logic signed [DATA_WIDTH:0] ALUOutE;
    logic signed [DATA_WIDTH-1:0] SrcAE;
    logic signed [DATA_WIDTH-1:0] SrcBE;
    logic signed [DATA_WIDTH-1:0] WriteDataE;
    logic branch_true;
    traps_t TrapsE;
    logic [DATA_WIDTH-1:0] FPUInAE,FPUInBE, FPUInAETemp;
    logic [ADDR_WIDTH-1:0] PCE;
    round_mode_t DynRoundMode,ActualRoundMode;

    risc_alu #(.DATA_WIDTH(DATA_WIDTH)) ALU
    (
        .SrcA(SrcAE),
        .SrcB(SrcBE),
        .alu_branch_control(branch_t'(funct3E)),
        .alu_control(ALUControlE),
        .Y(ALUOutE),
        .branch_true(branch_true)
    );

    risc_fpu #(.PRECISION(SINGLE),.STAGES(STAGES)) FPU
    (
        .clk(clk),
        .rst(rst),
        .valid(FPUValidE),
        .InA(FPUInAE),
        .InB(FPUInBE),
        .round_mode(ActualRoundMode),
        .operation(FPUControlE),
        .Overflow(OverflowM),
        .Underflow(UnderflowM),
        .RdF(RdFE),
        .NaN(NaNM),
        .Inf(InfM),
        .Zero(ZeroM),
        .InvalidDiv(InvalidDivM),
        .Result(FPUOutM),
        .busy(FPUBusyM),
        .done(FPUDoneM),
        .RdFOut(RdFM),
        .RegWriteOut(FPURegWriteM),
        .RegWrite(FPURegWriteE),
        .MoveOperation(MoveOperationE),
        .MoveOperationOut(MoveOperationM)
    );


    risc_mux4 #(.DATA_WIDTH(DATA_WIDTH)) M1 
    (
        .IN_1(RD1E),
        .IN_2(ResultW),
        .IN_3(ALUOutM),
        .IN_4(FPUOutW),
        .IN_5(FPUOutM),
        .IN_6(32'b0),
        .IN_7(32'b0),
        .IN_8(32'b0),
        .sel(ForwardAE),
        .Y(SrcAE)
    );


    risc_mux4 #(.DATA_WIDTH(DATA_WIDTH)) M2 
    (
        .IN_1(RD2E),
        .IN_2(ResultW),
        .IN_3(ALUOutM),
        .IN_4(FPUOutW),
        .IN_5(FPUOutM),
        .IN_6(32'b0),
        .IN_7(32'b0),
        .IN_8(32'b0),
        .sel(ForwardBE),
        .Y(WriteDataE)
    );

    risc_mux2 #(.DATA_WIDTH(DATA_WIDTH)) M3 
    (
        .IN_1(WriteDataE),
        .IN_2(SignImmE),
        .sel(ALUSrcE),
        .Y(SrcBE)
    );

    risc_mux3 #(.DATA_WIDTH(DATA_WIDTH)) F1
    (
        .IN_1(RD1FE),
        .IN_2(FPUOutW),
        .IN_3(FPUOutM),
        .IN_4(32'b0),
        .sel(ForwardFloatingAE),
        .Y(FPUInAETemp)
    );

    risc_mux3 #(.DATA_WIDTH(DATA_WIDTH)) F2
    (
        .IN_1(RD2FE),
        .IN_2(FPUOutW),
        .IN_3(FPUOutM),
        .IN_4(32'b0),
        .sel(ForwardFloatingBE),
        .Y(FPUInBE)
    );


    csr_file #(.DATA_WIDTH(DATA_WIDTH),.ADDR_WIDTH(ADDR_WIDTH)) CSR 
    (
        .clk(clk),
        .rst(rst),
        .CsrOperation(CsrOperationE),
        .Rs(Rs1E),
        .Traps(TrapsE),
        .mret(MRetE),
        .PC(PCE),
        .Address($unsigned(ALUOutE[ADDR_WIDTH-1:0])),
        .CsrAccess(CsrAccessE),
        .ExternalInterrupt(ExternalInterrupt),
        .TimerInterrupt(TimerInterrupt),
        .SoftwareInterrupt(SoftwareInterrupt),
        .CsrIn($unsigned(RD1E)),
        .CsrIndex(CsrIndexE),
        .CsrOutPC(CsrOutPC),
        .CsrOut(CsrOutM),
        .TrapIsSet(TrapIsSet),
        .RoundingMode(DynRoundMode)
    );

    always_ff @(posedge clk or negedge rst)
    begin
        if (!rst)
        begin
            RegWriteM <= 0;
            SelectorM <= ALUToReg;
            MemWriteM <= 0;
            ALUOutM <= 0;
            WriteDataM <= 0;
            RdM <= zero;
            funct3M <= 0;
            PCPlus4M <= 0;
        end
        else
        begin
            RegWriteM <= RegWriteE;
            SelectorM <= SelectorE;
            MemWriteM <= MemWriteE;
            ALUOutM <= ALUOutE[DATA_WIDTH-1:0];
            WriteDataM <= WriteDataE;
            RdM <= RdE;
            funct3M <= funct3E;
            PCPlus4M <= PCPlus4E;
        end
    end

    always_comb
    begin
        PCSrcE = (BranchE & branch_true) | JumpE;
    end

    always_comb
    begin
        PCE = PCPlus4E - 4;
    end


    always_comb
    begin
        TrapsE = NoTraps;
        if(|PCPlus4E[1:0])
        begin
            TrapsE = InstructionAddressMisalignedOrUserSoftwareInterrupt;
        end
        else if(EbreakE)
        begin
            TrapsE = BreakpointOrMachineSoftwareInterrupt;
        end
        else if(EcallE)
        begin
            TrapsE = EcallMOrMachineExternalInterrupt;
        end
        else if(IllegaleInstructionE)
        begin
            TrapsE = IllegalInstructionOrHypervisorSoftwareInterrupt;
        end
        else if(SelectorE == MemToReg)
        begin
            if((load_store_t'(funct3E) == W) && (|ALUOutE[1:0]))
            begin
                TrapsE = LoadAddressMisalignedOrUserSoftwareInterrupt;
            end
            else if(((load_store_t'(funct3E) == HW) | (load_store_t'(funct3E) == HWU)) && ALUOutE[0])
            begin
                TrapsE = LoadAddressMisalignedOrUserSoftwareInterrupt;
            end
        end
        else if(MemWriteE)
        begin
            if((load_store_t'(funct3E) == W) && (|ALUOutE[1:0]))
            begin
                TrapsE = StoreAddressMisalignedOrHyperVisorTimerInterrupt;
            end
            else if(ALUOutE[0] && (load_store_t'(funct3E) == HW))
            begin
                TrapsE = StoreAddressMisalignedOrHyperVisorTimerInterrupt;
            end
        end
    end

    always_comb
    begin
        FPUInAE = FPUInAETemp;
        if(MoveOperationE == RegToFPU)
            FPUInAE = SrcAE;
    end

    always_comb
    begin
        ActualRoundMode = RoundModeE;
        if(RoundModeE == DYN)
        begin
            ActualRoundMode = DynRoundMode;
        end
    end
   `ifdef VERIF
        bind FPU flp_wr FPU_bind
        (
            .*
        );

        bind CSR csr_wr CSR_bind
        (
            .*
        );
    `endif
endmodule
