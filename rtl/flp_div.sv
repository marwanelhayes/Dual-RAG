// =============================================================================
// flp_div.sv
// -----------------------------------------------------------------------------
// Floating-point division unit for RISC-V FPU.
//
// Responsibilities:
//   - Perform IEEE 754 single/double precision division
//   - Handle special cases (NaN, infinity, zero, division by zero)
//   - Support all rounding modes (RNE, RTZ, RDN, RUP, RMM, DYN)
//   - Multi-cycle iterative division with configurable stages
//   - Provide busy/done handshake for flow control
//
// Parameters:
//   - PRECISION: SINGLE (32-bit) or DOUBLE (64-bit)
//   - WIDTH: Total bit width (32 or 64)
//   - EXP_BITS: Exponent bits (8 for single, 11 for double)
//   - FRAC_BITS: Fraction bits (23 for single, 52 for double)
//   - BIAS: Exponent bias (127 for single, 1023 for double)
//   - EXTRA: Extra bits for precision (3 for single, 6 for double)
//   - STAGES: Number of division iterations (default 4)
// =============================================================================
import shared_pkg::*;

module flp_div
#(
    parameter flp_t PRECISION   = SINGLE,
    parameter int   WIDTH       = (PRECISION == SINGLE) ? 32 : 64,
    parameter int   EXP_BITS    = (PRECISION == SINGLE) ? 8  : 11,
    parameter int   FRAC_BITS   = (PRECISION == SINGLE) ? 23 : 52,
    parameter int   BIAS        = (PRECISION == SINGLE) ? 127: 1023,
    parameter int   EXTRA       = (PRECISION == SINGLE) ? 3 : 6,
    parameter int   STAGES      = 4
)(
    // ─── Operand inputs ───────────────────────────────────────────────────────
    input  logic [WIDTH-1:0] a,            // Dividend (numerator)
    input  logic [WIDTH-1:0] b,            // Divisor (denominator)

    // ─── Clock and reset ───────────────────────────────────────────────────────
    input  logic clk,                       // Clock signal
    input  logic rst,                       // Asynchronous reset

    // ─── Control signals ───────────────────────────────────────────────────
    input  logic valid,                     // Operation valid (start)
    input  round_mode_t round_mode,          // Rounding mode selection

    // ─── Result output ─────────────────────────────────────────────────────
    output logic [WIDTH-1:0] result,        // Floating-point quotient
    output logic busy,
    output logic done,
    output logic Overflow,
    output logic Underflow,
    output logic NaN,
    output logic Inf,
    output logic Zero,
    output logic invalid
);

    logic [WIDTH-1:0] a_reg, a_next;
    logic [WIDTH-1:0] b_reg, b_next;
    round_mode_t round_mode_reg, round_mode_next;
    flp_div_state_t state_reg, state_next;

    logic SignA_reg, SignA_next;
    logic SignB_reg, SignB_next;
    logic SignRes_reg, SignRes_next;
    logic AIsZero_reg, AIsZero_next;
    logic BIsZero_reg, BIsZero_next;
    logic AIsInf_reg, AIsInf_next;
    logic BIsInf_reg, BIsInf_next;
    logic AIsNan_reg, AIsNan_next;
    logic BIsNan_reg, BIsNan_next;
    logic AIsSub_reg, AIsSub_next;
    logic BIsSub_reg, BIsSub_next;
    logic signed [EXP_BITS+1:0] ExpA_reg, ExpA_next;
    logic signed [EXP_BITS+1:0] ExpB_reg, ExpB_next;
    logic signed [EXP_BITS+1:0] ExpDiff_reg, ExpDiff_next;
    logic [FRAC_BITS:0] MantA_reg, MantA_next;
    logic [FRAC_BITS:0] MantB_reg, MantB_next;
    logic DividerStart_reg, DividerStart_next;

    logic Overflow_reg, Overflow_next;
    logic Underflow_reg, Underflow_next;
    logic NaN_reg, NaN_next;
    logic Inf_reg, Inf_next;
    logic Zero_reg, Zero_next;
    logic Invalid_reg, Invalid_next;
    logic [WIDTH-1:0] result_reg, result_next;

    logic [$clog2(FRAC_BITS+1)-1:0] LeadZeroCountA, LeadZeroCountB;
    logic MantAIsZero, MantBIsZero;

    logic DividerBusy, DividerDone;
    logic [FRAC_BITS:0] DividerMantRes;
    logic DividerGuard, DividerRound, DividerSticky;

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

    function automatic logic [WIDTH-1:0] Rounding
    (
        input logic sign,
        input logic signed [EXP_BITS+1:0] exponent_in,
        input logic [FRAC_BITS:0] mantissa,
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
        logic signed [EXP_BITS+1:0] exponent_out;
        logic inexact;

        mantissa_in = mantissa;
        mantissa_out = {1'b0, mantissa};
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

        if (mantissa_out == (1 << (FRAC_BITS + 1)))
        begin
            mantissa_out = mantissa_out >> 1;
            exponent_out = exponent_out + 1'b1;
        end

        if (exponent_out[EXP_BITS+1])
        begin
            mantissa_out = mantissa_in >> (1 - exponent_out);
            Rounding = {sign, {EXP_BITS{1'b0}}, mantissa_out[FRAC_BITS-1:0]};
        end
        else if (exponent_out >= (2**EXP_BITS - 1))
        begin
            Rounding = {sign, {EXP_BITS{1'b1}}, {FRAC_BITS{1'b0}}};
        end
        else
        begin
            Rounding = {sign, exponent_out[EXP_BITS-1:0], mantissa_out[FRAC_BITS-1:0]};
        end
    endfunction: Rounding

    always_ff @(posedge clk or negedge rst)
    begin
        if (!rst)
        begin
            a_reg <= '0;
            b_reg <= '0;
            round_mode_reg <= RNE;
            state_reg <= INIT_DIV;
            SignA_reg <= 1'b0;
            SignB_reg <= 1'b0;
            SignRes_reg <= 1'b0;
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
            ExpDiff_reg <= '0;
            MantA_reg <= '0;
            MantB_reg <= '0;
            DividerStart_reg <= 1'b0;
            Overflow_reg <= 1'b0;
            Underflow_reg <= 1'b0;
            NaN_reg <= 1'b0;
            Inf_reg <= 1'b0;
            Zero_reg <= 1'b0;
            Invalid_reg <= 1'b0;
            result_reg <= '0;
        end
        else
        begin
            a_reg <= a_next;
            b_reg <= b_next;
            round_mode_reg <= round_mode_next;
            state_reg <= state_next;
            SignA_reg <= SignA_next;
            SignB_reg <= SignB_next;
            SignRes_reg <= SignRes_next;
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
            ExpDiff_reg <= ExpDiff_next;
            MantA_reg <= MantA_next;
            MantB_reg <= MantB_next;
            DividerStart_reg <= DividerStart_next;
            Overflow_reg <= Overflow_next;
            Underflow_reg <= Underflow_next;
            NaN_reg <= NaN_next;
            Inf_reg <= Inf_next;
            Zero_reg <= Zero_next;
            Invalid_reg <= Invalid_next;
            result_reg <= result_next;
        end
    end

    always_comb
    begin: comb
        logic [3:0] class_a;
        logic [3:0] class_b;
        logic [FRAC_BITS:0] mant_res;
        logic g;
        logic r;
        logic s;
        logic [WIDTH-1:0] rounded_result;
        logic [3:0] result_class;
        logic signed [EXP_BITS+1:0] exp_round;

        a_next = a_reg;
        b_next = b_reg;
        round_mode_next = round_mode_reg;
        state_next = state_reg;
        SignA_next = SignA_reg;
        SignB_next = SignB_reg;
        SignRes_next = SignRes_reg;
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
        ExpDiff_next = ExpDiff_reg;
        MantA_next = MantA_reg;
        MantB_next = MantB_reg;
        DividerStart_next = 1'b0;
        Overflow_next = Overflow_reg;
        Underflow_next = Underflow_reg;
        NaN_next = NaN_reg;
        Inf_next = Inf_reg;
        Zero_next = Zero_reg;
        Invalid_next = Invalid_reg;
        result_next = result_reg;

        class_a = '0;
        class_b = '0;
        mant_res = '0;
        g = 1'b0;
        r = 1'b0;
        s = 1'b0;
        rounded_result = '0;
        result_class = '0;
        exp_round = ExpDiff_reg;

        busy = (state_reg != IDLE_DIV) && (state_reg != DONE_DIV);
        done = (state_reg == DONE_DIV);
        result = result_reg;
        Overflow = Overflow_reg;
        Underflow = Underflow_reg;
        NaN = NaN_reg;
        Inf = Inf_reg;
        Zero = Zero_reg;
        invalid = Invalid_reg;

        case (state_reg)
            INIT_DIV:
            begin
                state_next = IDLE_DIV;
                Overflow_next = 1'b0;
                Underflow_next = 1'b0;
                NaN_next = 1'b0;
                Inf_next = 1'b0;
                Zero_next = 1'b0;
                Invalid_next = 1'b0;
                result_next = '0;
                busy = 1'b0;
                done = 1'b0;
            end

            IDLE_DIV:
            begin
                Overflow_next = 1'b0;
                Underflow_next = 1'b0;
                NaN_next = 1'b0;
                Inf_next = 1'b0;
                Zero_next = 1'b0;
                Invalid_next = 1'b0;
                result_next = '0;
                if (valid)
                begin
                    a_next = a;
                    b_next = b;
                    round_mode_next = round_mode;
                    state_next = SPLIT_DIV;
                end
            end

            SPLIT_DIV:
            begin
                SignA_next = a_reg[WIDTH-1];
                SignB_next = b_reg[WIDTH-1];
                SignRes_next = a_reg[WIDTH-1] ^ b_reg[WIDTH-1];
                ExpA_next = $signed({2'b00, a_reg[WIDTH-2 -: EXP_BITS]});
                ExpB_next = $signed({2'b00, b_reg[WIDTH-2 -: EXP_BITS]});
                class_a = classify_value(a_reg[WIDTH-2 -: EXP_BITS], a_reg[FRAC_BITS-1:0]);
                class_b = classify_value(b_reg[WIDTH-2 -: EXP_BITS], b_reg[FRAC_BITS-1:0]);
                {AIsSub_next, AIsNan_next, AIsInf_next, AIsZero_next} = class_a;
                {BIsSub_next, BIsNan_next, BIsInf_next, BIsZero_next} = class_b;

                if (class_a[2] || class_b[2])
                begin
                    result_next = 32'h7FC0_0000;
                    NaN_next = 1'b1;
                    Invalid_next = 1'b1;
                    state_next = DONE_DIV;
                end
                else if ((class_a[0] && class_b[0]) || (class_a[1] && class_b[1]))
                begin
                    result_next = 32'h7FC0_0000;
                    NaN_next = 1'b1;
                    Invalid_next = 1'b1;
                    state_next = DONE_DIV;
                end
                else if (!class_a[2] && !class_a[1] && !class_a[0] && class_b[0])
                begin
                    result_next = {a_reg[WIDTH-1] ^ b_reg[WIDTH-1], {EXP_BITS{1'b1}}, {FRAC_BITS{1'b0}}};
                    Inf_next = 1'b1;
                    state_next = DONE_DIV;
                end
                else if (class_a[0] && !class_b[0])
                begin
                    result_next = {a_reg[WIDTH-1] ^ b_reg[WIDTH-1], {EXP_BITS{1'b0}}, {FRAC_BITS{1'b0}}};
                    Zero_next = 1'b1;
                    state_next = DONE_DIV;
                end
                else if (class_a[1] && !class_b[1])
                begin
                    result_next = {a_reg[WIDTH-1] ^ b_reg[WIDTH-1], {EXP_BITS{1'b1}}, {FRAC_BITS{1'b0}}};
                    Inf_next = 1'b1;
                    state_next = DONE_DIV;
                end
                else if (!class_a[1] && class_b[1])
                begin
                    result_next = {a_reg[WIDTH-1] ^ b_reg[WIDTH-1], {EXP_BITS{1'b0}}, {FRAC_BITS{1'b0}}};
                    Zero_next = 1'b1;
                    state_next = DONE_DIV;
                end
                else
                begin
                    if (class_a[3])
                    begin
                        MantA_next = {a_reg[FRAC_BITS-1:0], 1'b0} << LeadZeroCountA;
                        ExpA_next = -$signed(LeadZeroCountA);
                    end
                    else
                    begin
                        MantA_next = {1'b1, a_reg[FRAC_BITS-1:0]};
                    end

                    if (class_b[3])
                    begin
                        MantB_next = {b_reg[FRAC_BITS-1:0], 1'b0} << LeadZeroCountB;
                        ExpB_next = -$signed(LeadZeroCountB);
                    end
                    else
                    begin
                        MantB_next = {1'b1, b_reg[FRAC_BITS-1:0]};
                    end

                    if ({2'b00, a_reg[WIDTH-2 -: EXP_BITS]} == '0 && !class_a[3])
                    begin
                        ExpA_next = 1;
                    end
                    if ({2'b00, b_reg[WIDTH-2 -: EXP_BITS]} == '0 && !class_b[3])
                    begin
                        ExpB_next = 1;
                    end

                    ExpDiff_next = ExpA_next - ExpB_next + BIAS;
                    DividerStart_next = 1'b1;
                    state_next = DIV_DIV;
                end
            end

            DIV_DIV:
            begin
                if (DividerDone)
                begin
                    state_next = ROUND_DIV;
                end
            end

            ROUND_DIV:
            begin
                mant_res = DividerMantRes;
                g = DividerGuard;
                r = DividerRound;
                s = DividerSticky;
                exp_round = ExpDiff_reg;

                if (!mant_res[FRAC_BITS] && (ExpDiff_reg > 0))
                begin
                    mant_res = {mant_res[FRAC_BITS-1:0], g};
                    g = r;
                    r = s;
                    exp_round = ExpDiff_reg - 1'b1;
                end

                rounded_result = Rounding(SignRes_reg, exp_round, mant_res, g, r, s, round_mode_reg);
                result_class = classify_value(rounded_result[WIDTH-2 -: EXP_BITS], rounded_result[FRAC_BITS-1:0]);
                result_next = rounded_result;
                NaN_next = result_class[2];
                Inf_next = result_class[1];
                Zero_next = result_class[0];
                if((!rounded_result[WIDTH-2 -: EXP_BITS]) && (!rounded_result[FRAC_BITS-1:0]))
                begin
                    Underflow_next = 1'b1;
                end
                else
                begin                    
                    Underflow_next = 1'b0;
                end
                Overflow_next = (&rounded_result[WIDTH-2 -: EXP_BITS]) && !result_class[2] && !result_class[0];
                Invalid_next = 1'b0;
                state_next = DONE_DIV;
            end

            DONE_DIV:
            begin
                state_next = IDLE_DIV;
            end

            default:
            begin
                state_next = IDLE_DIV;
            end
        endcase
    end: comb

    lzc_wr #(.WIDTH(FRAC_BITS + 1)) InputALZC(
        .A_in({a_reg[FRAC_BITS-1:0], 1'b0}),
        .leading_zeros(LeadZeroCountA),
        .is_zero(MantAIsZero)
    );

    lzc_wr #(.WIDTH(FRAC_BITS + 1)) InputBLZC(
        .A_in({b_reg[FRAC_BITS-1:0], 1'b0}),
        .leading_zeros(LeadZeroCountB),
        .is_zero(MantBIsZero)
    );

    non_restoring_divider_seq #(
        .FRAC_BITS(FRAC_BITS),
        .EXTRA(EXTRA),
        .STAGES(STAGES)
    ) divider_inst (
        .clk(clk),
        .rst(rst),
        .start(DividerStart_reg),
        .dividend(MantA_reg),
        .divisor(MantB_reg),
        .busy(DividerBusy),
        .done(DividerDone),
        .quotient(DividerMantRes),
        .guard(DividerGuard),
        .round(DividerRound),
        .sticky(DividerSticky)
    );

endmodule: flp_div

module non_restoring_divider_seq
#(
    parameter int FRAC_BITS = 23,
    parameter int EXTRA     = 3,
    parameter int STAGES    = 4
)(
    input  logic clk,
    input  logic rst,
    input  logic start,
    input  logic [FRAC_BITS:0] dividend,
    input  logic [FRAC_BITS:0] divisor,
    output logic busy,
    output logic done,
    output logic [FRAC_BITS:0] quotient,
    output logic guard,
    output logic round,
    output logic sticky
);

    localparam int TOTAL_ITERS = FRAC_BITS + EXTRA + 1;
    localparam int ITER_BITS = (TOTAL_ITERS > 1) ? $clog2(TOTAL_ITERS + 1) : 1;
    localparam int ITERS_PER_CYCLE = (TOTAL_ITERS + STAGES - 1) / STAGES;

    logic busy_reg, busy_next;
    logic done_reg, done_next;
    logic [FRAC_BITS:0] dividend_reg, dividend_next;
    logic [FRAC_BITS:0] divisor_reg, divisor_next;
    logic signed [FRAC_BITS+2:0] a_reg, a_next;
    logic signed [FRAC_BITS+2:0] m_reg, m_next;
    logic [FRAC_BITS+EXTRA:0] q_reg, q_next;
    logic [ITER_BITS-1:0] iter_reg, iter_next;
    logic [FRAC_BITS:0] quotient_reg, quotient_next;
    logic guard_reg, guard_next;
    logic round_reg, round_next;
    logic sticky_reg, sticky_next;

    always_ff @(posedge clk or negedge rst)
    begin
        if (!rst)
        begin
            busy_reg <= 1'b0;
            done_reg <= 1'b0;
            dividend_reg <= '0;
            divisor_reg <= '0;
            a_reg <= '0;
            m_reg <= '0;
            q_reg <= '0;
            iter_reg <= '0;
            quotient_reg <= '0;
            guard_reg <= 1'b0;
            round_reg <= 1'b0;
            sticky_reg <= 1'b0;
        end
        else
        begin
            busy_reg <= busy_next;
            done_reg <= done_next;
            dividend_reg <= dividend_next;
            divisor_reg <= divisor_next;
            a_reg <= a_next;
            m_reg <= m_next;
            q_reg <= q_next;
            iter_reg <= iter_next;
            quotient_reg <= quotient_next;
            guard_reg <= guard_next;
            round_reg <= round_next;
            sticky_reg <= sticky_next;
        end
    end

    always_comb
    begin: comb
        logic signed [FRAC_BITS+2:0] a_work;
        logic signed [FRAC_BITS+2:0] a_next_work;
        logic [FRAC_BITS+EXTRA:0] q_work;
        logic [ITER_BITS-1:0] iter_work;

        busy_next = busy_reg;
        done_next = 1'b0;
        dividend_next = dividend_reg;
        divisor_next = divisor_reg;
        a_next = a_reg;
        m_next = m_reg;
        q_next = q_reg;
        iter_next = iter_reg;
        quotient_next = quotient_reg;
        guard_next = guard_reg;
        round_next = round_reg;
        sticky_next = sticky_reg;

        a_work = a_reg;
        a_next_work = '0;
        q_work = q_reg;
        iter_work = iter_reg;

        if (start && !busy_reg)
        begin
            dividend_next = dividend;
            divisor_next = divisor;
            a_next = {2'b00, dividend};
            m_next = {2'b00, divisor};
            q_next = '0;
            iter_next = '0;
            quotient_next = '0;
            guard_next = 1'b0;
            round_next = 1'b0;
            sticky_next = 1'b0;
            busy_next = 1'b1;
        end
        else if (busy_reg)
        begin
            a_work = a_reg;
            q_work = q_reg;
            iter_work = iter_reg;

            for (int i = 0; i < ITERS_PER_CYCLE; i++)
            begin
                if (iter_work < TOTAL_ITERS)
                begin
                    if (a_work[FRAC_BITS+2])
                    begin
                        a_next_work = a_work + m_reg;
                    end
                    else
                    begin
                        a_next_work = a_work - m_reg;
                    end

                    q_work = {q_work[FRAC_BITS + EXTRA - 1:0], ~a_next_work[FRAC_BITS+2]};
                    a_work = a_next_work <<< 1;
                    iter_work = iter_work + 1'b1;
                end
            end

            a_next = a_work;
            q_next = q_work;
            iter_next = iter_work;

            if (iter_work >= TOTAL_ITERS)
            begin
                a_next_work = a_work >>> 1;
                if (a_next_work[FRAC_BITS+2])
                begin
                    a_next_work = a_next_work + m_reg;
                end

                quotient_next = q_work[FRAC_BITS + EXTRA : EXTRA];
                guard_next = q_work[EXTRA-1];
                round_next = q_work[EXTRA-2];
                sticky_next = |q_work[EXTRA-3:0] | (|a_next_work);
                busy_next = 1'b0;
                done_next = 1'b1;
            end
        end
    end: comb

    assign busy = busy_reg;
    assign done = done_reg;
    assign quotient = quotient_reg;
    assign guard = guard_reg;
    assign round = round_reg;
    assign sticky = sticky_reg;

endmodule: non_restoring_divider_seq
