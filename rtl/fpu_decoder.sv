// =============================================================================
// fpu_decoder.sv
// -----------------------------------------------------------------------------
// FPU instruction decoder for RISC-V processor.
//
// Responsibilities:
//   - Decode RISC-V floating-point instructions (RV32F)
//   - Generate FPU operation type
//   - Handle move operations between GPR and FPR
//   - Generate FPR write enable
//
// Supported operations:
//   - Arithmetic: FADD.S, FSUB.S, FMUL.S, FDIV.S, FSQRT.S
//   - Sign manipulation: FSGNJ.S, FSGNJN.S, FSGNJX.S
//   - Min/Max: FMIN.S, FMAX.S
//   - Compare: FEQ.S, FLT.S, FLE.S, FCLASS.S
//   - Conversion: FCVT.W.S, FCVT.WU.S, FCVT.S.W, FCVT.S.WU
//   - Move: FMV.X.W, FMV.W.X
// =============================================================================
/*
Instruction encoding reference:
0000000   rs2     rs1     rm      rd  1010011 FADD.S
0000100   rs2     rs1     rm      rd  1010011 FSUB.S
0001000   rs2     rs1     rm      rd  1010011 FMUL.S
0001100   rs2     rs1     rm      rd  1010011 FDIV.S
0101100   00000   rs1     rm      rd  1010011 FSQRT.S
0010000   rs2     rs1     000     rd  1010011 FSGNJ.S
0010000   rs2     rs1     001     rd  1010011 FSGNJN.S
0010000   rs2     rs1     010     rd  1010011 FSGNJX.S
0010100   rs2     rs1     000     rd  1010011 FMIN.S
0010100   rs2     rs1     001     rd  1010011 FMAX.S
1100000   00000   rs1     rm      rd  1010011 FCVT.W.S
1100000   00001   rs1     rm      rd  1010011 FCVT.WU.S
1110000   00000   rs1     000     rd  1010011 FMV.X.W
1010000   rs2     rs1     010     rd  1010011 FEQ.S
1010000   rs2     rs1     001     rd  1010011 FLT.S
1010000   rs2     rs1     000     rd  1010011 FLE.S
1110000   00000   rs1     001     rd  1010011 FCLASS.S
1101000   00000   rs1     rm      rd  1010011 FCVT.S.W
1101000   00001   rs1     rm      rd  1010011 FCVT.S.WU
1111000   00000   rs1     000     rd  1010011 FMV.W.X
*/

import shared_pkg::*;

module fpu_decoder
(
    // ─── Instruction fields ───────────────────────────────────────────────────
    input [2:0] funct3,             // Function field 3
    input [6:0] funct7,             // Function field 7
    input [4:0] Rs2,                // Source register 2 (for conversion)
    input FPUD,                    // FPU instruction flag from opcode decoder

    // ─── Outputs ───────────────────────────────────────────────────────────────
    output fpu_operation_t FPUControlD,        // FPU operation type
    output move_operation_t MoveOperationD,    // GPR/FPR move type
    output logic FPURegWriteD,                 // FPR write enable
    output logic Write,                       // GPR write enable (for moves)
    output logic FPUValidD                     // Valid FPU operation flag
);

    // ─── FPU operation decoding ──────────────────────────────────────────────
    always_comb
    begin
        FPUControlD = NOOPERATION;
        MoveOperationD = FPUToFPU; 
        FPURegWriteD = 1'b0;
        Write = 1'b0;

        if(FPUD)
        begin
            case(funct7)
                // ── Floating-point addition ───────────────────────────────────
                7'b0000000: begin
                                FPUControlD = FADD_S;
                                FPURegWriteD = 1'b1;
                            end

                // ── Floating-point subtraction ────────────────────────────────
                7'b0000100: begin
                                FPUControlD = FSUB_S;
                                FPURegWriteD = 1'b1;
                            end

                // ── Floating-point multiplication ─────────────────────────────
                7'b0001000: begin
                                FPUControlD = FMUL_S;
                                FPURegWriteD = 1'b1;
                            end

                // ── Floating-point division ───────────────────────────────────
                7'b0001100: begin
                                FPUControlD = FDIV_S;
                                FPURegWriteD = 1'b1;
                            end

                // ── Floating-point square root ────────────────────────────────
                7'b0101100: begin
                                FPUControlD = FSQRT_S;
                                FPURegWriteD = 1'b1;
                            end

                // ── Sign manipulation ────────────────────────────────────────
                7'b0010000: begin
                                case(funct3)
                                    3'b000: begin
                                                FPUControlD = FSGNJ_S;
                                                FPURegWriteD = 1'b1;
                                            end
                                    3'b001: begin
                                                FPUControlD = FSGNJN_S;
                                                FPURegWriteD = 1'b1;
                                            end
                                    3'b010: begin
                                                FPUControlD = FSGNJX_S;
                                                FPURegWriteD = 1'b1;
                                            end
                                endcase
                            end

                // ── Min/Max operations ────────────────────────────────────────
                7'b0010100: begin
                                case(funct3)
                                    3'b000: begin
                                                FPUControlD = FMIN_S;
                                                FPURegWriteD = 1'b1;
                                            end
                                    3'b001: begin
                                                FPUControlD = FMAX_S;
                                                FPURegWriteD = 1'b1;
                                            end
                                endcase
                            end

                // ── Integer conversion (FP to integer) ────────────────────────
                7'b1100000: begin
                                MoveOperationD = FPUToReg;
                                Write = 1'b1;
                                case(Rs2)
                                    5'b00000: FPUControlD = FCVT_W_S;
                                    5'b00001: FPUControlD = FCVT_WU_S;
                                endcase
                            end

                // ── Move from FPR (to GPR) / classification ───────────────────
                7'b1110000: begin
                                if(Rs2 == 5'b00000)
                                begin
                                    case(funct3)
                                        3'b000: begin
                                                    MoveOperationD = FPUToReg;
                                                    Write = 1'b1; 
                                                    FPUControlD = FMV_X_S;
                                                end
                                        3'b001: begin
                                                    FPUControlD = FCLASS_S;
                                                    FPURegWriteD = 1'b1;
                                                end
                                    endcase
                                end
                            end

                // ── Comparison operations ──────────────────────────────────────
                7'b1010000: begin
                                case(funct3)
                                    3'b010: begin
                                                FPUControlD = FEQ_S;
                                                FPURegWriteD = 1'b1;
                                            end
                                    3'b001: begin
                                                FPUControlD = FLT_S;
                                                FPURegWriteD = 1'b1;
                                            end
                                    3'b000: begin
                                                FPUControlD = FLE_S;
                                                FPURegWriteD = 1'b1;
                                            end
                                endcase
                            end

                // ── Integer conversion (integer to FP) ────────────────────────
                7'b1101000: begin
                                MoveOperationD = RegToFPU;
                                case(Rs2)
                                    5'b00000: begin
                                                FPUControlD = FCVT_S_W;
                                                FPURegWriteD = 1'b1;
                                            end
                                    5'b00001: begin
                                                FPUControlD = FCVT_S_WU;
                                                FPURegWriteD = 1'b1;
                                            end
                                endcase
                            end

                // ── Move to FPR (from GPR) ───────────────────────────────────
                7'b1111000: begin 
                                FPUControlD = FMV_S_X;
                                MoveOperationD = RegToFPU;
                                FPURegWriteD = 1'b1;
                            end
            endcase
        end
    end

    // ─── Valid FPU operation flag ───────────────────────────────────────────
    always_comb
    begin
        FPUValidD = FPURegWriteD || Write;
    end
endmodule : fpu_decoder
