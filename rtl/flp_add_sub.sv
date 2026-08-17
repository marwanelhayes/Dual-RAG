// =============================================================================
// flp_add_sub.sv
// -----------------------------------------------------------------------------
// Floating-point addition/subtraction unit for RISC-V FPU.
//
// Responsibilities:
//   - Perform IEEE 754 single/double precision addition
//   - Perform IEEE 754 single/double precision subtraction
//   - Handle special cases (NaN, infinity, zero, denormal)
//   - Support all rounding modes (RNE, RTZ, RDN, RUP, RMM, DYN)
//   - Multi-cycle pipelined operation with busy/done handshake
//
// Parameters:
//   - PRECISION: SINGLE (32-bit) or DOUBLE (64-bit)
//   - WIDTH: Total bit width (32 or 64)
//   - EXP_BITS: Exponent bits (8 for single, 11 for double)
//   - FRAC_BITS: Fraction bits (23 for single, 52 for double)
//   - BIAS: Exponent bias (127 for single, 1023 for double)
// =============================================================================
import shared_pkg::*;

module flp_add_sub
#(
    parameter flp_t PRECISION   = SINGLE,
    parameter int   WIDTH       = (PRECISION == SINGLE) ? 32 : 64,
    parameter int   EXP_BITS    = (PRECISION == SINGLE) ? 8  : 11,
    parameter int   FRAC_BITS   = (PRECISION == SINGLE) ? 23 : 52,
    parameter int   BIAS        = (PRECISION == SINGLE) ? 127: 1023
)(
    // ─── Operand inputs ───────────────────────────────────────────────────────
    input   logic [WIDTH-1:0] a,           // First floating-point operand
    input   logic [WIDTH-1:0] b,           // Second floating-point operand

    // ─── Clock and reset ───────────────────────────────────────────────────────
    input   logic clk,                     // Clock signal
    input   logic rst,                     // Asynchronous reset

    // ─── Control signals ───────────────────────────────────────────────────
    input   logic valid,                   // Operation valid (start)
    input   mode_t mode,                   // ADD (0) or SUB (1) mode
    input   round_mode_t round_mode,       // Rounding mode selection

    // ─── Status outputs ───────────────────────────────────────────────────
    output  logic busy,                    // Operation in progress
    output  logic done,                    // Operation complete
    output  logic Overflow,                // Result overflowed
    output  logic Underflow,               // Result underflowed
    output  logic NaN,                     // Result is NaN
    output  logic Inf,                     // Result is infinity
    output  logic Zero,                    // Result is zero

    // ─── Result output ─────────────────────────────────────────────────────
    output  logic [WIDTH-1:0] result        // Floating-point result
);

    logic [WIDTH-1:0] a_reg, a_next;
    logic [WIDTH-1:0] b_reg, b_next;
    mode_t mode_reg, mode_next;
    round_mode_t round_mode_reg, round_mode_next;
    flp_add_sub_state_t state_reg, state_next;

    logic SignA_reg, SignA_next;
    logic SignB_reg, SignB_next;
    logic AIsZero_reg, AIsZero_next;
    logic BIsZero_reg, BIsZero_next;
    logic AIsInf_reg, AIsInf_next;
    logic BIsInf_reg, BIsInf_next;
    logic AIsNan_reg, AIsNan_next;
    logic BIsNan_reg, BIsNan_next;
    logic AIsSub_reg, AIsSub_next;
    logic BIsSub_reg, BIsSub_next;
    logic [EXP_BITS-1:0] ExpA_reg, ExpA_next;
    logic [EXP_BITS-1:0] ExpB_reg, ExpB_next;

    logic [FRAC_BITS:0] MantA_reg, MantA_next;
    logic [FRAC_BITS:0] MantB_reg, MantB_next;
    logic [EXP_BITS-1:0] ExpRes_reg, ExpRes_next;
    logic guard_reg, guard_next;
    logic round_reg, round_next;
    logic sticky_reg, sticky_next;
    logic effective_sub_reg, effective_sub_next;

    logic Overflow_reg, Overflow_next;
    logic Underflow_reg, Underflow_next;
    logic NaN_reg, NaN_next;
    logic Inf_reg, Inf_next;
    logic Zero_reg, Zero_next;
    logic [WIDTH-1:0] result_reg, result_next;

    logic [3:0] class_a;
    logic [3:0] class_b;
    logic [EXP_BITS:0] ExpDiff;
    logic [FRAC_BITS:0] MantA_local;
    logic [FRAC_BITS:0] MantB_local;
    logic [FRAC_BITS+1:0] mant_sum;
    logic [FRAC_BITS:0] mant_diff;
    logic [EXP_BITS-1:0] exp_local;
    logic sign_local;
    logic guard_local;
    logic round_local;
    logic sticky_local;
    logic [WIDTH-1:0] pre_round_result;
    logic [WIDTH-1:0] rounded_result;
    logic [3:0] result_class;

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

    function automatic logic [FRAC_BITS + 3:0] AlignWithShiftGRS
    (
        input logic [FRAC_BITS:0] mant_in,
        input logic [EXP_BITS:0] sh
    );
        logic [FRAC_BITS:0] mant_out;
        logic g;
        logic r;
        logic s;

        mant_out = mant_in;
        g = 1'b0;
        r = 1'b0;
        s = 1'b0;

        if (sh == '0)
        begin
            mant_out = mant_in;
        end
        else if (sh <= (FRAC_BITS + 1))
        begin
            mant_out = mant_in >> sh;
            g = mant_in[sh-1];
            if (sh >= 2)
            begin
                r = mant_in[sh-2];
            end
            if (sh >= 3)
            begin
                for (int i = 0; i < FRAC_BITS; i++)
                begin
                    if (i < (sh - 2))
                    begin
                        s = s | mant_in[i];
                    end
                end
            end
        end
        else
        begin
            mant_out = '0;
            s = |mant_in;
        end

        AlignWithShiftGRS = {mant_out, g, r, s};
    endfunction: AlignWithShiftGRS

    function automatic logic [WIDTH-1:0] Rounding
    (
        input logic [WIDTH-1:0] in,
        input logic g,
        input logic r,
        input logic s,
        input round_mode_t mode_in
    );
        logic lsb;
        logic tie;
        logic round_up;
        logic [FRAC_BITS:0] mantissa_in;
        logic [FRAC_BITS+1:0] mantissa_out;
        logic [EXP_BITS-1:0] exponent_in;
        logic [EXP_BITS-1:0] exponent_out;
        logic sign;
        logic inexact;

        sign = in[WIDTH-1];
        mantissa_in = {1'b1, in[FRAC_BITS-1:0]};
        exponent_in = in[WIDTH-2 -: EXP_BITS];
        exponent_out = exponent_in;

        lsb = mantissa_in[0];
        tie = g && !r && !s;
        inexact = g | r | s;
        round_up = 1'b0;

        case (mode_in)
            RNE: round_up = (g && (r || s)) || (tie && lsb);
            RTZ: round_up = 1'b0;
            RDN: round_up = inexact && sign;
            RUP: round_up = inexact && !sign;
            RMM: round_up = g;
            default: round_up = (g && (r || s)) || (tie && lsb);
        endcase

        if (round_up)
        begin
            mantissa_out = mantissa_in + 1'b1;
        end
        else
        begin
            mantissa_out = mantissa_in;
        end

        if (mantissa_out[FRAC_BITS+1])
        begin
            mantissa_out = mantissa_out >> 1;
            exponent_out = exponent_in + 1'b1;
        end

        Rounding = {sign, exponent_out, mantissa_out[FRAC_BITS-1:0]};
    endfunction: Rounding

    always_ff @(posedge clk or negedge rst)
    begin
        if (!rst)
        begin
            a_reg <= '0;
            b_reg <= '0;
            mode_reg <= ADD_FLP;
            round_mode_reg <= RNE;
            state_reg <= INIT;
            SignA_reg <= 1'b0;
            SignB_reg <= 1'b0;
            AIsZero_reg <= 1'b0;
            BIsZero_reg <= 1'b0;
            AIsInf_reg <= 1'b0;
            BIsInf_reg <= 1'b0;
            AIsNan_reg <= 1'b0;
            BIsNan_reg <= 1'b0;
            AIsSub_reg <= 1'b0;
            BIsSub_reg <= 1'b0;
            ExpA_reg <= '0;
            ExpB_reg <= '0;
            MantA_reg <= '0;
            MantB_reg <= '0;
            ExpRes_reg <= '0;
            guard_reg <= 1'b0;
            round_reg <= 1'b0;
            sticky_reg <= 1'b0;
            effective_sub_reg <= 1'b0;
            Overflow_reg <= 1'b0;
            Underflow_reg <= 1'b0;
            NaN_reg <= 1'b0;
            Inf_reg <= 1'b0;
            Zero_reg <= 1'b0;
            result_reg <= '0;
        end
        else
        begin
            a_reg <= a_next;
            b_reg <= b_next;
            mode_reg <= mode_next;
            round_mode_reg <= round_mode_next;
            state_reg <= state_next;
            SignA_reg <= SignA_next;
            SignB_reg <= SignB_next;
            AIsZero_reg <= AIsZero_next;
            BIsZero_reg <= BIsZero_next;
            AIsInf_reg <= AIsInf_next;
            BIsInf_reg <= BIsInf_next;
            AIsNan_reg <= AIsNan_next;
            BIsNan_reg <= BIsNan_next;
            AIsSub_reg <= AIsSub_next;
            BIsSub_reg <= BIsSub_next;
            ExpA_reg <= ExpA_next;
            ExpB_reg <= ExpB_next;
            MantA_reg <= MantA_next;
            MantB_reg <= MantB_next;
            ExpRes_reg <= ExpRes_next;
            guard_reg <= guard_next;
            round_reg <= round_next;
            sticky_reg <= sticky_next;
            effective_sub_reg <= effective_sub_next;
            Overflow_reg <= Overflow_next;
            Underflow_reg <= Underflow_next;
            NaN_reg <= NaN_next;
            Inf_reg <= Inf_next;
            Zero_reg <= Zero_next;
            result_reg <= result_next;
        end
    end

    always_comb
    begin: comb
        a_next = a_reg;
        b_next = b_reg;
        mode_next = mode_reg;
        round_mode_next = round_mode_reg;
        state_next = state_reg;
        SignA_next = SignA_reg;
        SignB_next = SignB_reg;
        AIsZero_next = AIsZero_reg;
        BIsZero_next = BIsZero_reg;
        AIsInf_next = AIsInf_reg;
        BIsInf_next = BIsInf_reg;
        AIsNan_next = AIsNan_reg;
        BIsNan_next = BIsNan_reg;
        AIsSub_next = AIsSub_reg;
        BIsSub_next = BIsSub_reg;
        ExpA_next = ExpA_reg;
        ExpB_next = ExpB_reg;
        MantA_next = MantA_reg;
        MantB_next = MantB_reg;
        ExpRes_next = ExpRes_reg;
        guard_next = guard_reg;
        round_next = round_reg;
        sticky_next = sticky_reg;
        effective_sub_next = effective_sub_reg;
        Overflow_next = Overflow_reg;
        Underflow_next = Underflow_reg;
        NaN_next = NaN_reg;
        Inf_next = Inf_reg;
        Zero_next = Zero_reg;
        result_next = result_reg;

        class_a = '0;
        class_b = '0;
        ExpDiff = '0;
        MantA_local = '0;
        MantB_local = '0;
        mant_sum = '0;
        mant_diff = '0;
        exp_local = '0;
        sign_local = 1'b0;
        guard_local = 1'b0;
        round_local = 1'b0;
        sticky_local = 1'b0;
        pre_round_result = '0;
        rounded_result = '0;
        result_class = '0;

        busy = (state_reg != IDLE) && (state_reg != DONE);
        done = (state_reg == DONE);
        Overflow = Overflow_reg;
        Underflow = Underflow_reg;
        NaN = NaN_reg;
        Inf = Inf_reg;
        Zero = Zero_reg;
        result = result_reg;

        case (state_reg)
            INIT:
            begin
                state_next = IDLE;
                Overflow_next = 1'b0;
                Underflow_next = 1'b0;
                NaN_next = 1'b0;
                Inf_next = 1'b0;
                Zero_next = 1'b0;
                result_next = '0;
                busy = 1'b0;
                done = 1'b0;
                if(valid)
                begin
                    a_next = a;
                    b_next = b;
                    mode_next = mode;
                    round_mode_next = round_mode;
                    state_next = SPLIT;
                    busy = 1'b1;
                end
            end

            IDLE:
            begin
                Overflow_next = 1'b0;
                Underflow_next = 1'b0;
                NaN_next = 1'b0;
                Inf_next = 1'b0;
                Zero_next = 1'b0;
                result_next = '0;
                a_next = 'b0;
                b_next = 'b0;
                state_next = IDLE;
                if (valid)
                begin
                    a_next = a;
                    b_next = b;
                    mode_next = mode;
                    round_mode_next = round_mode;
                    state_next = SPLIT;
                    busy = 1'b1;
                end
            end

            SPLIT:
            begin
                SignA_next = a_reg[WIDTH-1];
                SignB_next = b_reg[WIDTH-1];
                ExpA_next = a_reg[WIDTH-2 -: EXP_BITS];
                ExpB_next = b_reg[WIDTH-2 -: EXP_BITS];
                class_a = classify_value(a_reg[WIDTH-2 -: EXP_BITS], a_reg[FRAC_BITS-1:0]);
                class_b = classify_value(b_reg[WIDTH-2 -: EXP_BITS], b_reg[FRAC_BITS-1:0]);
                {AIsSub_next, AIsNan_next, AIsInf_next, AIsZero_next} = class_a;
                {BIsSub_next, BIsNan_next, BIsInf_next, BIsZero_next} = class_b;

                Overflow_next = 1'b0;
                Underflow_next = 1'b0;
                NaN_next = 1'b0;
                Inf_next = 1'b0;
                Zero_next = 1'b0;
                result_next = '0;

                if (class_a[2] || class_b[2])
                begin
                    result_next = {1'b0, {EXP_BITS{1'b1}}, {1'b1, {(FRAC_BITS-1){1'b0}}}};
                    NaN_next = 1'b1;
                    state_next = DONE;
                end
                else if (class_a[1] && class_b[1])
                begin
                    if ((mode_reg == ADD_FLP && (a_reg[WIDTH-1] != b_reg[WIDTH-1])) ||
                        (mode_reg == SUB_FLP && (a_reg[WIDTH-1] == b_reg[WIDTH-1])))
                    begin
                        result_next = {1'b0, {EXP_BITS{1'b1}}, {1'b1, {(FRAC_BITS-1){1'b0}}}};
                        NaN_next = 1'b1;
                    end
                    else
                    begin
                        result_next = {a_reg[WIDTH-1], {EXP_BITS{1'b1}}, {FRAC_BITS{1'b0}}};
                        Inf_next = 1'b1;
                    end
                    state_next = DONE;
                end
                else if (class_a[1])
                begin
                    result_next = {a_reg[WIDTH-1], {EXP_BITS{1'b1}}, {FRAC_BITS{1'b0}}};
                    Inf_next = 1'b1;
                    state_next = DONE;
                end
                else if (class_b[1])
                begin
                    if (mode_reg == ADD_FLP)
                    begin
                        result_next = {b_reg[WIDTH-1], {EXP_BITS{1'b1}}, {FRAC_BITS{1'b0}}};
                    end
                    else
                    begin
                        result_next = {~b_reg[WIDTH-1], {EXP_BITS{1'b1}}, {FRAC_BITS{1'b0}}};
                    end
                    Inf_next = 1'b1;
                    state_next = DONE;
                end
                else if (class_a[0] && class_b[0])
                begin
                    result_next = {a_reg[WIDTH-1] & b_reg[WIDTH-1], {EXP_BITS{1'b0}}, {FRAC_BITS{1'b0}}};
                    Zero_next = 1'b1;
                    state_next = DONE;
                end
                else if (class_a[0])
                begin
                    if (mode_reg == ADD_FLP)
                    begin
                        result_next = b_reg;
                    end
                    else
                    begin
                        result_next = {~b_reg[WIDTH-1], b_reg[WIDTH-2:0]};
                    end
                    result_class = classify_value(result_next[WIDTH-2 -: EXP_BITS], result_next[FRAC_BITS-1:0]);
                    NaN_next = result_class[2];
                    Inf_next = result_class[1];
                    Zero_next = result_class[0];
                    state_next = DONE;
                end
                else if (class_b[0])
                begin
                    result_next = a_reg;
                    result_class = classify_value(result_next[WIDTH-2 -: EXP_BITS], result_next[FRAC_BITS-1:0]);
                    NaN_next = result_class[2];
                    Inf_next = result_class[1];
                    Zero_next = result_class[0];
                    state_next = DONE;
                end
                else
                begin
                    state_next = ALIGN;
                end
            end

            ALIGN:
            begin
                if (AIsSub_reg)
                begin
                    MantA_local = {1'b0, a_reg[FRAC_BITS-1:0]};
                end
                else
                begin
                    MantA_local = {1'b1, a_reg[FRAC_BITS-1:0]};
                end

                if (BIsSub_reg)
                begin
                    MantB_local = {1'b0, b_reg[FRAC_BITS-1:0]};
                end
                else
                begin
                    MantB_local = {1'b1, b_reg[FRAC_BITS-1:0]};
                end

                guard_local = 1'b0;
                round_local = 1'b0;
                sticky_local = 1'b0;

                if (ExpA_reg > ExpB_reg)
                begin
                    ExpDiff = ExpA_reg - ExpB_reg;
                    {MantB_local, guard_local, round_local, sticky_local} = AlignWithShiftGRS(MantB_local, ExpDiff);
                    ExpRes_next = ExpA_reg;
                end
                else
                begin
                    ExpDiff = ExpB_reg - ExpA_reg;
                    {MantA_local, guard_local, round_local, sticky_local} = AlignWithShiftGRS(MantA_local, ExpDiff);
                    ExpRes_next = ExpB_reg;
                end

                MantA_next = MantA_local;
                MantB_next = MantB_local;
                guard_next = guard_local;
                round_next = round_local;
                sticky_next = sticky_local;
                effective_sub_next = (mode_reg == ADD_FLP) ? (SignA_reg != SignB_reg) : (SignA_reg == SignB_reg);
                if ((mode_reg == ADD_FLP) ? (SignA_reg != SignB_reg) : (SignA_reg == SignB_reg))
                begin
                    state_next = TRUESUB;
                end
                else
                begin
                    state_next = TRUEADD;
                end
            end

            TRUEADD:
            begin
                mant_sum = MantA_reg + MantB_reg;
                exp_local = ExpRes_reg;
                sign_local = SignA_reg;
                guard_local = guard_reg;
                round_local = round_reg;
                sticky_local = sticky_reg;

                if (mant_sum[FRAC_BITS+1] && (exp_local != '0))
                begin
                    guard_local = mant_sum[0];
                    round_local = guard_reg;
                    sticky_local = round_reg | sticky_reg;
                    mant_sum = mant_sum >> 1;
                    exp_local = exp_local + 1'b1;
                end

                if (exp_local == {EXP_BITS{1'b1}})
                begin
                    result_next = {sign_local, {EXP_BITS{1'b1}}, {FRAC_BITS{1'b0}}};
                    Overflow_next = 1'b1;
                    Underflow_next = 1'b0;
                    NaN_next = 1'b0;
                    Inf_next = 1'b1;
                    Zero_next = 1'b0;
                end
                else
                begin
                    pre_round_result = {sign_local, exp_local, mant_sum[FRAC_BITS-1:0]};
                    rounded_result = Rounding(pre_round_result, guard_local, round_local, sticky_local, round_mode_reg);
                    result_class = classify_value(rounded_result[WIDTH-2 -: EXP_BITS], rounded_result[FRAC_BITS-1:0]);
                    result_next = rounded_result;
                    Overflow_next = result_class[1] && !result_class[2] && !result_class[0];
                    Underflow_next = 1'b0;
                    NaN_next = result_class[2];
                    Inf_next = result_class[1];
                    Zero_next = result_class[0];
                end
                state_next = DONE;
            end

            TRUESUB:
            begin
                exp_local = ExpRes_reg;
                guard_local = guard_reg;
                round_local = round_reg;
                sticky_local = sticky_reg;

                if (MantA_reg < MantB_reg)
                begin
                    mant_diff = MantB_reg - MantA_reg;
                    sign_local = ~SignA_reg;
                end
                else
                begin
                    mant_diff = MantA_reg - MantB_reg;
                    sign_local = SignA_reg;
                end

                if (mant_diff == '0)
                begin
                    result_next = {1'b0, {EXP_BITS{1'b0}}, {FRAC_BITS{1'b0}}};
                    Overflow_next = 1'b0;
                    Underflow_next = 1'b0;
                    NaN_next = 1'b0;
                    Inf_next = 1'b0;
                    Zero_next = 1'b1;
                end
                else
                begin
                    for (int i = 0; i < FRAC_BITS; i++)
                    begin
                        if ((mant_diff[FRAC_BITS] == 1'b0) && (exp_local > '0))
                        begin
                            mant_diff = mant_diff << 1;
                            exp_local = exp_local - 1'b1;
                        end
                    end

                    if (exp_local == '0)
                    begin
                        result_next = {sign_local, {EXP_BITS{1'b0}}, {FRAC_BITS{1'b0}}};
                        Overflow_next = 1'b0;
                        Underflow_next = 1'b1;
                        NaN_next = 1'b0;
                        Inf_next = 1'b0;
                        Zero_next = 1'b1;
                    end
                    else
                    begin
                        pre_round_result = {sign_local, exp_local, mant_diff[FRAC_BITS-1:0]};
                        rounded_result = Rounding(pre_round_result, guard_local, round_local, sticky_local, round_mode_reg);
                        result_class = classify_value(rounded_result[WIDTH-2 -: EXP_BITS], rounded_result[FRAC_BITS-1:0]);
                        result_next = rounded_result;
                        Overflow_next = 1'b0;
                        Underflow_next = 1'b0;
                        NaN_next = result_class[2];
                        Inf_next = result_class[1];
                        Zero_next = result_class[0];
                    end
                end
                state_next = DONE;
            end
            DONE:
            begin
                state_next = IDLE;
            end

            default:
            begin
                state_next = IDLE;
            end
        endcase
    end: comb

endmodule: flp_add_sub
