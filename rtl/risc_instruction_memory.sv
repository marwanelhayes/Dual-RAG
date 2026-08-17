// =============================================================================
// risc_instruction_memory.sv
// -----------------------------------------------------------------------------
// Instruction memory (ROM) with direct read and AXI4 slave read interface.
// =============================================================================
import shared_pkg::*;

module risc_instruction_memory
#(
    parameter int DATA_WIDTH = 32,
    parameter int ADDR_WIDTH = 32,
    parameter int AXI_SIZE   = 4
)
(
    input  logic                  clk,
    input  logic                  rst,

    // AXI4 slave write address channel
    input  logic [ADDR_WIDTH-1:0] MAWAddr,
    input  logic [7:0]            MAWLen,
    input  logic [AXI_SIZE-1:0]   MAWSize,
    input  axi_burst_t            MAWBurst,
    input  logic                  MAWValid,
    output logic                  MAWReady,

    // AXI4 slave write data channel
    input  logic [DATA_WIDTH-1:0] MWData,
    input  logic [(DATA_WIDTH/8)-1:0] MWStrb,
    input  logic                  MWLast,
    input  logic                  MWValid,
    output logic                  MWReady,

    // AXI4 slave write response channel
    input  logic                  MBReady,
    output axi_resp_t             MBResp,
    output logic                  MBValid,

    // AXI4 slave read address channel
    input  logic [ADDR_WIDTH-1:0] MARAddr,
    input  logic [7:0]            MARLen,
    input  logic [AXI_SIZE-1:0]   MARSize,
    input  axi_burst_t            MARBurst,
    input  logic                  MARValid,
    output logic                  MARReady,

    // AXI4 slave read data channel
    input  logic                  MRReady,
    output logic [DATA_WIDTH-1:0] MRRData,
    output axi_resp_t             MRRResp,
    output logic                  MRRLast,
    output logic                  MRRValid
);

    localparam int DEPTH = 2**(ADDR_WIDTH-2);

    logic [DATA_WIDTH-1:0] mem [DEPTH];
    logic [ADDR_WIDTH-3:0] word_aligned_addr;
    logic [ADDR_WIDTH-3:0] raddr, raddr_next;
    logic [AXI_SIZE-1:0]   rsize;
    axi_burst_t            rburst;
    logic [7:0]            rlen, rcnt, wlen, wcnt;
    logic                  read_active, write_active;

    assign raddr_next = (rburst == FIXED) ? raddr : (raddr + ((1 << rsize) >> 2));
    assign MARReady = !read_active;
    assign MAWReady = !write_active && !MBValid;
    assign MWReady  = write_active;
    assign MBResp   = AXI_SLVERR;
    assign word_aligned_addr = MARAddr[ADDR_WIDTH-1:2];

    initial
    begin
        $readmemb("/home/marwan-ahmed/Work/RiscV/Python/binary1.txt", mem);
    end

    always_ff @(posedge clk or negedge rst)
    begin
        if(!rst)
        begin
            read_active <= 1'b0;
            write_active <= 1'b0;
            MRRValid <= 1'b0;
            MRRData <= '0;
            MRRResp <= AXI_OKAY;
            MRRLast <= 1'b0;
            MBValid <= 1'b0;
            raddr <= '0;
            rlen <= '0;
            rcnt <= '0;
            wlen <= '0;
            wcnt <= '0;
            rsize <= '0;
            rburst <= FIXED;
        end
        else
        begin
            if(MARValid && MARReady)
            begin
                read_active <= 1'b1;
                MRRValid <= 1'b1;
                MRRData <= mem[word_aligned_addr];
                MRRResp <= AXI_OKAY;
                MRRLast <= (MARLen == '0);
                raddr <= word_aligned_addr;
                rlen <= MARLen;
                rsize <= MARSize;
                rburst <= MARBurst;
                rcnt <= '0;
            end
            else if(MRRValid && MRReady)
            begin
                if(MRRLast)
                begin
                    read_active <= 1'b0;
                    MRRValid <= 1'b0;
                    MRRLast <= 1'b0;
                end
                else
                begin
                    rcnt <= rcnt + 1'b1;
                    raddr <= raddr_next;
                    MRRData <= mem[raddr_next];
                    MRRResp <= AXI_OKAY;
                    MRRLast <= ((rcnt + 1'b1) == rlen);
                end
            end

            if(MAWValid && MAWReady)
            begin
                write_active <= 1'b1;
                wlen <= MAWLen;
                wcnt <= '0;
            end
            else if(MWValid && MWReady)
            begin
                wcnt <= wcnt + 1'b1;
                if(MWLast || (wcnt == wlen))
                begin
                    write_active <= 1'b0;
                    MBValid <= 1'b1;
                end
            end
            if(MBValid && MBReady)
                MBValid <= 1'b0;
        end
    end

endmodule
