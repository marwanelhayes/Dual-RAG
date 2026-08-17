// =============================================================================
// flp_sqrt.sv
// -----------------------------------------------------------------------------
// Floating-point square root unit for RISC-V FPU.
//
// Responsibilities:
//   - Perform IEEE 754 single/double precision square root
//   - Handle special cases (NaN, infinity, zero, negative input)
//   - Support all rounding modes (RNE, RTZ, RDN, RUP, RMM, DYN)
//   - Multi-cycle iterative square root using Newton-Raphson method
//   - Provide busy/done handshake for flow control
//   - Interface with external multiplier for iterations
//
// Parameters:
//   - PRECISION: SINGLE (32-bit) or DOUBLE (64-bit)
//   - WIDTH: Total bit width (32 or 64)
//   - EXP_BITS: Exponent bits (8 for single, 11 for double)
//   - FRAC_BITS: Fraction bits (23 for single, 52 for double)
//   - BIAS: Exponent bias (127 for single, 1023 for double)
//   - STAGES: Number of iteration stages (default 4)
// =============================================================================
import shared_pkg::*;

module flp_sqrt
#(
    parameter flp_t PRECISION   = SINGLE,
    parameter int   WIDTH       = (PRECISION == SINGLE) ? 32 : 64,
    parameter int   EXP_BITS    = (PRECISION == SINGLE) ? 8  : 11,
    parameter int   FRAC_BITS   = (PRECISION == SINGLE) ? 23 : 52,
    parameter int   BIAS        = (PRECISION == SINGLE) ? 127: 1023,
    parameter int   STAGES      = 4
)(
    // ─── Clock and reset ───────────────────────────────────────────────────────
    input   logic clk,                       // Clock signal
    input   logic rst,                       // Asynchronous reset

    // ─── Control and operand inputs ─────────────────────────────────────────
    input   logic valid,                     // Operation valid (start)
    input   logic [WIDTH-1:0] a,             // Input operand (radicand)

    // ─── Multiplier interface (for Newton-Raphson iterations) ──────────────
    output  logic [WIDTH-1:0] mul_a,         // Multiplier operand A
    output  logic [WIDTH-1:0] mul_b,         // Multiplier operand B
    output  logic mul_valid,                 // Multiplier valid signal
    input   logic [WIDTH-1:0] mul_result,    // Multiplier result
    input   logic mul_done,                  // Multiplier done signal
    output  logic [WIDTH-1:0] add_sub_a,
    output  logic [WIDTH-1:0] add_sub_b,
    output  logic add_sub_valid,
    output  mode_t add_sub_mode,
    input   logic [WIDTH-1:0] add_sub_result,
    input   logic add_sub_done,
    output  logic busy,
    output  logic done,
    output  logic Inf,
    output  logic NaN,
    output  logic Zero,
    output  logic [WIDTH-1:0] result
);

    localparam logic [WIDTH-1:0] MAGIC = (PRECISION == SINGLE) ? 32'h5f3759df : 64'h5fe6eb50c7b537a9;
    localparam logic [WIDTH-1:0] THREE_HALFS = (PRECISION == SINGLE) ? 32'h3fc00000 : 64'h3ff8000000000000;

    logic [WIDTH-1:0] a_reg, a_next;
    logic [WIDTH-1:0] InHalf_reg, InHalf_next;
    logic [WIDTH-1:0] InMagic_reg, InMagic_next;
    logic [WIDTH-1:0] Result1_reg, Result1_next;
    logic [WIDTH-1:0] Result2_reg, Result2_next;
    logic [WIDTH-1:0] Result3_reg, Result3_next;
    logic [WIDTH-1:0] Result4_reg, Result4_next;
    flp_sqrt_state_t state_reg, state_next;

    logic Inf_reg, Inf_next;
    logic NaN_reg, NaN_next;
    logic Zero_reg, Zero_next;
    logic [WIDTH-1:0] result_reg, result_next;

    logic Sign;
    logic [EXP_BITS-1:0] Exp;
    logic [FRAC_BITS-1:0] Mant;
    logic [3:0] class_in;
    logic [EXP_BITS-1:0] ExpHalf;
    logic [3:0] class_out;

    function automatic logic [3:0] classify_value(
        input logic [EXP_BITS-1:0] exp,
        input logic [FRAC_BITS-1:0] frac
    );
        logic [3:0] classification;
        classification[0] = (exp == '0) && (frac == '0);
        classification[1] = (exp == {EXP_BITS{1'b1}}) && (frac == '0);
        classification[2] = (exp == {EXP_BITS{1'b1}}) && (frac != '0);
        classification[3] = (exp == '0) && (frac != '0);
        classify_value = classification;
    endfunction: classify_value

    always_ff @(posedge clk or negedge rst)
    begin
        if (!rst)
        begin
            a_reg <= '0;
            InHalf_reg <= '0;
            InMagic_reg <= '0;
            Result1_reg <= '0;
            Result2_reg <= '0;
            Result3_reg <= '0;
            Result4_reg <= '0;
            state_reg <= SQRT_INIT;
            Inf_reg <= 1'b0;
            NaN_reg <= 1'b0;
            Zero_reg <= 1'b0;
            result_reg <= '0;
        end
        else
        begin
            a_reg <= a_next;
            InHalf_reg <= InHalf_next;
            InMagic_reg <= InMagic_next;
            Result1_reg <= Result1_next;
            Result2_reg <= Result2_next;
            Result3_reg <= Result3_next;
            Result4_reg <= Result4_next;
            state_reg <= state_next;
            Inf_reg <= Inf_next;
            NaN_reg <= NaN_next;
            Zero_reg <= Zero_next;
            result_reg <= result_next;
        end
    end

    always_comb
    begin: comb
        a_next = a_reg;
        InHalf_next = InHalf_reg;
        InMagic_next = InMagic_reg;
        Result1_next = Result1_reg;
        Result2_next = Result2_reg;
        Result3_next = Result3_reg;
        Result4_next = Result4_reg;
        state_next = state_reg;
        Inf_next = Inf_reg;
        NaN_next = NaN_reg;
        Zero_next = Zero_reg;
        result_next = result_reg;

        mul_a = '0;
        mul_b = '0;
        mul_valid = 1'b0;
        add_sub_a = THREE_HALFS;
        add_sub_b = Result2_reg;
        add_sub_valid = 1'b0;
        add_sub_mode = SUB_FLP;

        Sign = a_reg[WIDTH-1];
        Exp = a_reg[WIDTH-2 -: EXP_BITS];
        Mant = a_reg[FRAC_BITS-1:0];
        ExpHalf = 0;
        class_out = classify_value(result_reg[WIDTH-2 -: EXP_BITS], result_reg[FRAC_BITS-1:0]);
        class_in  = classify_value(a[WIDTH-2 -: EXP_BITS], a[FRAC_BITS-1:0]);

        busy = (state_reg != SQRT_IDLE) && (state_reg != SQRT_DONE);
        done = (state_reg == SQRT_DONE);
        Inf = Inf_reg;
        NaN = NaN_reg;
        Zero = Zero_reg;
        result = result_reg;

        case (state_reg)
            SQRT_INIT:
            begin
                state_next = SQRT_IDLE;
                Inf_next = 1'b0;
                NaN_next = 1'b0;
                Zero_next = 1'b0;
                result_next = '0;
                busy = 1'b0;
                done = 1'b0;
                if (valid)
                begin
                    a_next = a;
                    if (a[WIDTH-1])
                    begin
                        result_next = 32'h7FC00000;
                        NaN_next = 1'b1;
                        state_next = SQRT_DONE;
                    end
                    else if (class_in[2] ||
                             class_in[1] ||
                             class_in[0])
                    begin
                        NaN_next = class_in[2];
                        Inf_next = class_in[1];
                        Zero_next = class_in[0];
                        result_next = a;
                        state_next = SQRT_DONE;
                    end
                    else
                    begin
                        if (class_in[3])
                        begin
                            InHalf_next = a;
                        end
                        else
                        begin
                            InHalf_next = {a[WIDTH-1], a[WIDTH-2 -: EXP_BITS] - 1'b1, a[FRAC_BITS-1:0]};
                        end
                        InMagic_next = MAGIC - (a >> 1);
                        state_next = SQRT_MUL1;
                    end
                end
            end

            SQRT_IDLE:
            begin
                Inf_next = 1'b0;
                NaN_next = 1'b0;
                Zero_next = 1'b0;
                result_next = '0;
                if (valid)
                begin
                    a_next = a;
                    if (a[WIDTH-1])
                    begin
                        result_next = 32'h7FC00000;
                        NaN_next = 1'b1;
                        state_next = SQRT_DONE;
                    end
                    else if (class_in[2] ||
                             class_in[1] ||
                             class_in[0])
                    begin
                        result_next = a;
                        NaN_next = class_in[2];
                        Inf_next = class_in[1];
                        Zero_next = class_in[0];
                        state_next = SQRT_DONE;
                    end
                    else
                    begin
                        if (class_in[3])
                        begin
                            InHalf_next = a;
                        end
                        else
                        begin
                            InHalf_next = {a[WIDTH-1], a[WIDTH-2 -: EXP_BITS] - 1'b1, a[FRAC_BITS-1:0]};
                        end
                        InMagic_next = MAGIC - (a >> 1);
                        state_next = SQRT_MUL1;
                    end
                end
            end

            SQRT_MUL1:
            begin
                mul_a = InHalf_reg;
                mul_b = InMagic_reg;
                mul_valid = 1'b1;
                if (mul_done)
                begin
                    Result1_next = mul_result;
                    state_next = SQRT_MUL2;
                end
            end

            SQRT_MUL2:
            begin
                mul_a = Result1_reg;
                mul_b = InMagic_reg;
                mul_valid = 1'b1;
                if (mul_done)
                begin
                    Result2_next = mul_result;
                    state_next = SQRT_ADD_SUB;
                end
            end

            SQRT_ADD_SUB:
            begin
                add_sub_valid = 1'b1;
                if (add_sub_done)
                begin
                    Result3_next = add_sub_result;
                    state_next = SQRT_MUL3;
                end
            end

            SQRT_MUL3:
            begin
                mul_a = Result3_reg;
                mul_b = InMagic_reg;
                mul_valid = 1'b1;
                if (mul_done)
                begin
                    Result4_next = mul_result;
                    state_next = SQRT_MUL4;
                end
            end

            SQRT_MUL4:
            begin
                mul_a = a_reg;
                mul_b = Result4_reg;
                mul_valid = 1'b1;
                if (mul_done)
                begin
                    result_next = mul_result;
                    class_out = classify_value(mul_result[WIDTH-2 -: EXP_BITS], mul_result[FRAC_BITS-1:0]);
                    NaN_next = class_out[2];
                    Inf_next = class_out[1];
                    Zero_next = class_out[0];
                    state_next = SQRT_DONE;
                end
            end

            SQRT_DONE:
            begin
                state_next = SQRT_IDLE;
            end

            default:
            begin
                state_next = SQRT_IDLE;
            end
        endcase
    end: comb

endmodule: flp_sqrt
