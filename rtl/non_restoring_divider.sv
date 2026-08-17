// =============================================================================
// non_restoring_divider.sv
// -----------------------------------------------------------------------------
// Non-restoring divider for integer division in ALU.
// =============================================================================
module non_restoring_divider #(parameter WIDTH = 32) 
(
    input   logic signed [WIDTH-1:0] dividend,
    input   logic signed [WIDTH-1:0] divisor,
    input   logic sign,
    output  logic signed [WIDTH-1:0] quotient,
    output  logic signed [WIDTH-1:0] remainder
);

    logic signed [WIDTH:0] A;
    logic [WIDTH-1:0] Q;
    logic [WIDTH-1:0] M;
    int i;

    assign NegativeQuotient = dividend[WIDTH-1] ^ divisor[WIDTH-1];

    always_comb 
    begin
        A = '0;
        Q = '0;
        M = '0;

        if(sign)
        begin
            Q = dividend[WIDTH-1] ? -dividend : dividend;
            M = divisor[WIDTH-1] ? -divisor : divisor;
        end
        else
        begin
            Q = dividend;
            M = divisor;
        end

        foreach(dividend[i])
        begin: non_restoring_division
            if(A[WIDTH])
            begin
                A = {A[WIDTH-1:0], Q[WIDTH-1]};
                A = A + M; 
            end
            else
            begin 
                A = {A[WIDTH-1:0], Q[WIDTH-1]};
                A = A - M;
            end
            Q = {Q[WIDTH-2:0], ~A[WIDTH]};
        end: non_restoring_division

        if(A[WIDTH])
        begin:restoration
            A = A + M ;
        end: restoration

        if(divisor==0)
        begin : DivByZero
            if(sign)
            begin
                quotient = -1;
                remainder = dividend;
            end
            else
            begin
                quotient = $unsigned(-1);
                remainder = dividend;
            end  
        end : DivByZero
        else if (sign && (dividend == (1 << (WIDTH-1))) && (divisor == -1)) 
        begin : Overflow
            quotient  = dividend; 
            remainder = '0;
        end : Overflow
        else
        begin: NormalCase
            if (sign)
            begin:SignCase
                quotient = NegativeQuotient ? -Q : Q;
                remainder = dividend[WIDTH-1]? -A : A;
            end:SignCase
            else
            begin:UnsignedCase
                quotient = Q;
                remainder = A;
            end:UnsignedCase
        end: NormalCase
    end

endmodule