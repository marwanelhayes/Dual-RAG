// =============================================================================
// alu_decoder.sv
// -----------------------------------------------------------------------------
// ALU operation decoder for RISC-V processor.
//
// Responsibilities:
//   - Decode funct3 and funct7 fields to generate ALU operation
//   - Handle base ISA operations (ADD, SUB, shifts, logical, compare)
//   - Handle M extension (multiply/divide operations)
// =============================================================================
import shared_pkg::*;

module alu_decoder
#(
    parameter int ALU_SUB_CONTROL_WIDTH = 2
)
(
    // ─── Instruction fields ───────────────────────────────────────────────────
    input [2:0] funct3,                     // Function field 3
    input [6:0] funct7,                      // Function field 7
    input [ALU_SUB_CONTROL_WIDTH-1:0] ALUControl,  // Sub-operation code from opcode decoder

    // ─── Output ───────────────────────────────────────────────────────────────
    output alu_operation_t ALUControlD      // Detailed ALU operation
);

    // ─── Internal wires ───────────────────────────────────────────────────────
    logic Unsign;                            // Unsigned operation flag

    // ─── Unsigned operation detection ────────────────────────────────────────
    always_comb
    begin
        Unsign = funct3[2] & funct3[1];     // funct3[2:1] = 2'b11 indicates unsigned
    end

    // ─── ALU operation decoding ───────────────────────────────────────────────
    always_comb
    begin
        ALUControlD = ADD;    // Default operation
        case(ALUControl)
            // ── Branch-type operations ───────────────────────────────────────
            2'b00: begin
                        if(Unsign)
                            ALUControlD = SLTU;    // BLTU
                        else
                            ALUControlD = SUB;    // BGE, BGT
                    end
            // ── Immediate-type operations ───────────────────────────────────
            2'b01: begin
                        case(funct3)
                            3'b000: ALUControlD = ADD;     // ADDI
                            3'b010: ALUControlD = SLT;     // SLTI
                            3'b011: ALUControlD = SLTU;    // SLTIU
                            3'b100: ALUControlD = XOR;     // XORI
                            3'b110: ALUControlD = OR;      // ORI
                            3'b111: ALUControlD = AND;     // ANDI
                        endcase
                    end
            // ── R-type operations ────────────────────────────────────────────
            2'b10: begin
                        // Standard RV32I operations
                        if(!(|funct7))
                        begin
                            case(funct3)
                                3'b000: ALUControlD = ADD;     // ADD
                                3'b001: ALUControlD = SLL;     // SLL
                                3'b010: ALUControlD = SLT;     // SLT
                                3'b011: ALUControlD = SLTU;    // SLTU
                                3'b100: ALUControlD = XOR;     // XOR
                                3'b101: ALUControlD = SRL;     // SRL/SRA
                                3'b110: ALUControlD = OR;      // OR
                                3'b111: ALUControlD = AND;     // AND
                            endcase
                        end
                        // RV32I with bit 5 set (SUB, SRA)
                        else if(funct7[5])
                        begin
                            case(funct3)
                                3'b000: ALUControlD = SUB;     // SUB
                                3'b101: ALUControlD = SRA;     // SRA
                            endcase
                        end
                        // RV32M extension (multiply/divide)
                        else if(funct7[0])
                        begin
                            case(funct3)
                                3'b000: ALUControlD = MUL;     // MUL
                                3'b001: ALUControlD = MULH;    // MULH
                                3'b010: ALUControlD = MULHSU;  // MULHSU
                                3'b011: ALUControlD = MULHU;   // MULHU
                                3'b100: ALUControlD = DIV;     // DIV
                                3'b101: ALUControlD = DIVU;    // DIVU
                                3'b110: ALUControlD = REM;     // REM
                                3'b111: ALUControlD = REMU;    // REMU
                            endcase
                        end
                    end
            default: ALUControlD = ADD;
        endcase
    end
endmodule
