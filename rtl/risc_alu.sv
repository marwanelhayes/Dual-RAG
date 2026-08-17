// =============================================================================
// risc_alu.sv
// -----------------------------------------------------------------------------
// Arithmetic Logic Unit for RISC-V processor.
//
// Responsibilities:
//   - Perform arithmetic operations (ADD, SUB)
//   - Perform logical operations (AND, OR, XOR)
//   - Perform shift operations (SLL, SRL, SRA)
//   - Perform comparison operations (SLT, SLTU)
//   - Perform multiplication (MUL, MULH, MULHSU, MULHU)
//   - Perform division and remainder (DIV, DIVU, REM, REMU)
//   - Evaluate branch conditions
//
// Instantiates:
//   - wallace_tree: fast multiplication tree
//   - non_restoring_divider: division hardware
// =============================================================================
import shared_pkg::*;

module risc_alu 
#(
    parameter int DATA_WIDTH = 32
) 
(
    // ─── Operand inputs ───────────────────────────────────────────────────────
    input signed [DATA_WIDTH-1:0] SrcA,    // First operand
    input signed [DATA_WIDTH-1:0] SrcB,   // Second operand

    // ─── Operation select ─────────────────────────────────────────────────────
    input alu_operation_t alu_control,      // ALU operation type
    input branch_t alu_branch_control,      // Branch comparison type

    // ─── Outputs ─────────────────────────────────────────────────────────────
    output logic branch_true,              // Branch condition met
    output logic signed [DATA_WIDTH:0] Y   // Result (extra bit for overflow)
);

    localparam int LOG_WIDTH = $clog2(DATA_WIDTH);

    // ─── Internal wires – arithmetic units ───────────────────────────────────
    logic signed [2*DATA_WIDTH - 1:0] MulOutput;  // Multiplication result
    logic signed [DATA_WIDTH-1:0] DivOutput;      // Division quotient
    logic signed [DATA_WIDTH-1:0] RemOutput;      // Division remainder
    mul_sel_t mul_sel;                             // Multiplication select
    logic Divsign;                                 // Division sign flag

    // ─── Multiplication operand selection ───────────────────────────────────
    always_comb 
    begin
        if((alu_control == MUL) || (alu_control == MULH))
            mul_sel = sign;                        // Signed × Signed
        else if((alu_control == MULHU))
            mul_sel = unsign;                      // Unsigned × Unsigned
        else if((alu_control == MULHSU))
            mul_sel = signXunsign;                 // Signed × Unsigned
        else
            mul_sel = error;
    end

    // ─── Division sign selection ─────────────────────────────────────────────
    always_comb 
    begin
        if((alu_control == REM) || (alu_control == DIV))
            Divsign = 1;                           // Signed division
        else
            Divsign = 0;                           // Unsigned division
    end

    // ─── risc_alu: multiplication unit ───────────────────────────────────────
    wallace_tree #(.WIDTH(DATA_WIDTH)) mul (
        .A(SrcA),
        .B(SrcB),
        .sel(mul_sel),
        .P(MulOutput)
    );

    // ─── risc_alu: division unit ─────────────────────────────────────────────
    non_restoring_divider #(.WIDTH(DATA_WIDTH)) div (
        .dividend(SrcA),
        .divisor(SrcB),
        .sign(Divsign),
        .quotient(DivOutput),
        .remainder(RemOutput)
    );

    // ─── ALU operation execution ────────────────────────────────────────────
    always_comb 
    begin
        Y = '0;
        case(alu_control)
            // ── Arithmetic ───────────────────────────────────────────────────
            ADD     :   Y = $signed(SrcA) + $signed(SrcB); 
            SUB     :   Y = $signed(SrcA) - $signed(SrcB);
            // ── Logical ─────────────────────────────────────────────────────
            AND     :   Y = SrcA & SrcB;
            OR      :   Y = SrcA | SrcB;
            XOR     :   Y = SrcA ^ SrcB;
            // ── Compare ─────────────────────────────────────────────────────
            SLT     :   begin
                            if($signed(SrcA) < $signed(SrcB))
                                Y[0] = 1'b1;
                            else
                                Y[0] = 1'b0;
                        end
            SLTU    :   begin
                            if($unsigned(SrcA) < $unsigned(SrcB))
                                Y[0] = 1'b1;
                            else
                                Y[0] = 1'b0;
                        end
            // ── Shift ────────────────────────────────────────────────────────
            SLL     :   Y = $unsigned(SrcA) << $unsigned(SrcB[LOG_WIDTH-1:0]);
            SRL     :   Y = $unsigned(SrcA) >> $unsigned(SrcB[LOG_WIDTH-1:0]);
            SRA     :   Y = $signed(SrcA) >>> $unsigned(SrcB[LOG_WIDTH-1:0]);
            // ── Multiply ─────────────────────────────────────────────────────
            MUL     :   Y = MulOutput[DATA_WIDTH-1:0];
            MULH    :   Y = MulOutput[2*DATA_WIDTH-1:DATA_WIDTH];
            MULHSU  :   Y = MulOutput[2*DATA_WIDTH-1:DATA_WIDTH];
            MULHU   :   Y = $unsigned(MulOutput[2*DATA_WIDTH-1:DATA_WIDTH]);
            // ── Divide ────────────────────────────────────────────────────────
            DIV     :   Y = DivOutput;
            REM     :   Y = RemOutput;
            DIVU    :   Y = $unsigned(DivOutput);
            REMU    :   Y = $unsigned(RemOutput);
        endcase 
    end

    // ─── Branch condition evaluation ────────────────────────────────────────
    always_comb 
    begin
        branch_true = 1'b0;
        case(alu_branch_control)
            // ── Equality branches ───────────────────────────────────────────
            BEQ     :   branch_true = ~|Y;         // Equal: result is zero
            BNE     :   branch_true = |Y;           // Not equal: result is non-zero
            // ── Signed comparison ───────────────────────────────────────────
            BLT     :   branch_true = Y[DATA_WIDTH];   // Less than (signed)
            BGE     :   branch_true = ~Y[DATA_WIDTH];   // Greater or equal (signed)
            // ── Unsigned comparison ─────────────────────────────────────────
            BLTU    :   branch_true = Y[0];             // Less than (unsigned)
            BGEU    :   branch_true = ~Y[0];            // Greater or equal (unsigned)
        endcase
    end

endmodule
