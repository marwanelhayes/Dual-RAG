// =============================================================================
// data_memory.sv
// -----------------------------------------------------------------------------
// Synchronous data memory (RAM) with direct access and AXI4 slave interface.
// =============================================================================
import shared_pkg::*;

module risc_data_memory
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

    localparam int DEPTH = 2**(ADDR_WIDTH - 2); // Assuming word-addressable memory
    localparam int STRB_WIDTH = DATA_WIDTH / 8;

    logic [DATA_WIDTH-1:0] mem [DEPTH];
    logic [ADDR_WIDTH-1:0] raddr, raddr_next, waddr;
    logic [AXI_SIZE-1:0]   rsize, wsize;
    axi_burst_t            rburst, wburst;
    logic [7:0]            rlen, rcnt, wlen, wcnt;
    logic                  read_active, write_active;

    assign raddr_next = (rburst == FIXED) ? raddr : raddr + (1 << rsize);
    assign MARReady = !read_active;
    assign MAWReady = !write_active && !MBValid;
    assign MWReady  = write_active;
    assign MBResp   = AXI_OKAY;

    initial 
    begin
        foreach (mem[idx]) mem[idx] = '0;
    end
    
    always @(posedge clk or negedge rst)
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
            waddr <= '0;
            rlen <= '0;
            rcnt <= '0;
            wlen <= '0;
            wcnt <= '0;
            rsize <= '0;
            wsize <= '0;
            rburst <= FIXED;
            wburst <= FIXED;
        end
        else
        begin
            if(MARValid && MARReady)
            begin
                read_active <= 1'b1;
                MRRValid <= 1'b1;
                MRRData <= mem[MARAddr[ADDR_WIDTH-1:2]];
                MRRResp <= AXI_OKAY;
                MRRLast <= (MARLen == '0);
                raddr <= MARAddr;
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
                    MRRData <= mem[raddr_next[ADDR_WIDTH-1:2]];
                    MRRResp <= AXI_OKAY;
                    MRRLast <= ((rcnt + 1'b1) == rlen);
                end
            end

            if(MAWValid && MAWReady)
            begin
                write_active <= 1'b1;
                waddr <= MAWAddr;
                wlen <= MAWLen;
                wsize <= MAWSize;
                wburst <= MAWBurst;
                wcnt <= '0;
            end
            else if(MWValid && MWReady)
            begin
                for(int i = 0; i < STRB_WIDTH; i = i + 1)
                    if(MWStrb[i])
                        mem[waddr[ADDR_WIDTH-1:2]][8*i +: 8] <= MWData[8*i +: 8];
                wcnt <= wcnt + 1'b1;
                waddr <= (wburst == FIXED) ? waddr : waddr + (1 << wsize);
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
