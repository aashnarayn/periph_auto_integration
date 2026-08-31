/***************************************************************************
 * Project           	         : Shakti Devt Board
 * Name of the file	     	     : rtc_v1.bsv
 * Brief Description of file     : Source hardware code for Real Time Clock (RTC). 
 *                                 Acts as a 1Hz clock with set, read functionality,
 *                                 calendar support (in software) and an alarm interrupt. 
 * Name of Author    	         : Aashrith S Narayn
 * Email ID                      : aashnarayn22@gmail.com

 Copyright (C) 2019  IIT Madras. All rights reserved.

 This program is free software: you can redistribute it and/or modify
 it under the terms of the GNU General Public License as published by
 the Free Software Foundation, either version 3 of the License, or
 (at your option) any later version.

 This program is distributed in the hope that it will be useful,
 but WITHOUT ANY WARRANTY; without even the implied warranty of
 MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 GNU General Public License for more details.

 You should have received a copy of the GNU General Public License
 along with this program.  If not, see <https://www.gnu.org/licenses/>.
 ****************************************************************************/

/**
 * ==================================================================================
 * SHAKTI RTCv1: 48-BIT HIGH-PRECISION REAL TIME CLOCK
 * ==================================================================================
 * * 1. OVERVIEW & ARCHITECTURE
 * ----------------------------------------------------------------------------------
 * This module implements a 48-bit (32+16) RTC designed for Shakti C-class SoCs. 
 * The timekeeping for this RTC is valid till the year ~8.9 million, with hardware support for 2038/2106 rollovers.
 * To ensure stability across asynchronous boundaries, it utilizes a 2-Domain 
 * Clock Architecture with a dedicated Mailbox Handshake for CDC (Clock Domain Crossing):
 * * - Domain A (Fast): AXI4-Lite Bus Clock (System/CPU Clock, e.g., 40MHz).
 * * - Domain B (Slow): 32.768kHz Crystal Input (Oscillator Domain).
 * 
 * * 2. SPECIFICATIONS & OSCILLATOR CHARACTERISTICS
 * ----------------------------------------------------------------------------------
 * * Oscillator:      Epson SG-3030CM (CMOS Output).
 * * Physical Drift:  Characterized at +14.136 PPM (Physical clock runs fast).
 * * Deficit Factor:  Hardware loses approx. 565 CPU cycles per 1Hz tick relative 
 * to a nominal 40MHz system clock.
 * * Time Range:       48-bit Total (32-bit Counter + 16-bit Epoch Extension).
 * - Starts: UNIX Epoch (1970-01-01 00:00:00).
 * - Range:  Valid until Year ~8,921,556.
 * - Rollovers: Handles 2038 (Signed) and 2106 (Unsigned) in hardware.
 *
 *  * 3. CDC & TIMING CONSTRAINTS (Verified on Silicon)
 * ----------------------------------------------------------------------------------
 * * Handshake Latency: Synchronizing control signals (Load/Reset) across the 
 * asynchronous boundary requires a multi-cycle handshake.
 * - Pulse Width: High/Low signals must persist for >3 slow clock cycles (~92us).
 * - Busy State: After writing to Control (Reset/Update), software MUST poll 
 * the Busy bit or wait for a minimum of 200us before the next AXI transaction.
 * * Interrupt Latency: Alarm IRQs are generated in the 32.768kHz domain. Due to 
 * synchronization stages, expect a 30-60us jitter relative to the 1Hz edge.
 * 
 * * 4. HARDWARE INTEGRATION & CDC PROTOCOL (BSV/RTL Implementation)
 * ----------------------------------------------------------------------------------
 * The RTC module operates across two distinct asynchronous clock domains. 
 * Correct hardware integration requires adherence to the following constraints:
 * 
 * Clock Domains:
 *   - CLK (Fast): The AXI4-Lite bus clock (typically 40MHz - 100MHz).
 *   - CLK_LOW (Slow): The 32.768kHz crystal oscillator input.
 * 
 * CDC Handshake Protocol:
 *   - The hardware utilizes a request-acknowledge "Mailbox" synchronizer.
 *   - When writing to Control or Time-Loading registers, the 'Busy' bit (Bit 31 
 *     of the Control Register) is asserted. 
 *   - Software/Hardware-Master MUST poll this bit and wait for it to clear 
 *     before initiating the next transaction.
 *   - Latency: Expect ~6-7 Slow-Clock cycles (~200us) for a full round-trip 
 *     synchronization.
 * 
 * Oscillator Characterization:
 *   - Validated using Epson SG-3030CM silicon. 
 *   - Measured Drift: +14.136 PPM (Physical hardware runs fast).
 *   - Compensation: Software must apply a deficit of 565 cycles per second 
 *     relative to a 40MHz system clock to maintain wall-clock accuracy.
 * 
 * * Sequence of Execution (Hardware/Driver Level):
 *   ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
 * Time Initialization:
 *   1. Reset the RTC, set Load Time 0, Enable 0 (Register Val: 0x00000001)
 *   2. Set the 16-bit Trim value in the Control Register to match oscillator (with drift PPM in mind).
 *   3. Load the 32-bit Seconds into the Time Holding Register (0x10).
 *   4. Load the 16-bit Epoch into the Epoch Holding Register (0x20).
 *   5. Set the 'Update' bit in the Control Register and wait for 'Busy' to clear.
 *   6. Set the 'Enable' bit to start the RTC. (Register Val: 0x00000003)
 * 
 * Reading Time (Atomicity Rule):
 *   1. To prevent torn-reads during 1Hz rollovers, read the Epoch (0x18), 
 *      then Time (0x08), then Epoch (0x18) again.
 *   2. If the Epoch values match, the read is atomic. If not, retry the sequence.
 * 
 * Interrupt Configuration:
 *   1. Configure the 32-bit Alarm Target Register (0x28).
 *   2. Ensure the Alarm is within the current 136-year epoch window.
 *      - For 32-Bit Signed Integer   (int32_t):  Valid till Tuesday, January 19, 2038, at 03:14:07 UTC.
 *      - For 32-Bit Unsigned Integer (uint32_t): Valid till Sunday,  February 7, 2106, at 06:28:15 UTC. 
 *   3. Clear the interrupt by writing to the IRQ_CLEAR bit in the Control Register.
 *
 * * 5. SOFTWARE INTEGRATION PROCESS (REQUIRED WORKFLOW IN GCSDK USING DRIVER)
 * ----------------------------------------------------------------------------------
 * To ensure timing accuracy and prevent data corruption, follow this sequence:
 * Call the rtc driver functions through the imported header (rtcv1.h). 
 * The driver abstracts the handshake and timing requirements, but the sequence must be followed:
 * * STEP 1: INITIALIZATION
 * Call rtc_init() and rtc_get_instance(0). Perform rtc_soft_reset() and poll 
 * until Busy bit clears (~200us).
 * * STEP 2: DRIFT COMPENSATION
 * Apply the 14.1 PPM correction. Software must use algebraic inverse scaling 
 * (adding the 565-cycle deficit) when setting or reading "True Time" to 
 * neutralize the SG-3030CM frequency error. This has been done in the driver's primitives.
 * * STEP 3: ATOMIC READS (The 136-Year Fault)
 * When accessing the 48-bit time (32-bit Time + 16-bit Epoch), software MUST 
 * employ the High-Low-High read pattern:
 * 1. Read Upper (Epoch) -> 2. Read Lower (Seconds) -> 3. Read Upper again.
 * If Upper changed, repeat. This prevents "Torn Reads" during rollovers.
 * * STEP 4: ALARM MANAGEMENT
 * The hardware comparator is 32-bit. Alarms can only be set within the 
 * current 136-year epoch. For deep-time alarms beyond the current epoch, 
 * software must manage a virtual alarm queue or have another workaround strategy.
 * 
 * * 6. REGISTER MAP (64-bit Aligned AXI Offsets) (refer to rtc.defines)
 * ----------------------------------------------------------------------------------
 * * 0x00 (RW) (OLD: 0x00) : Control Register [0: Enable | 1: Reset | 2: IRQ Clear | 3-18: Trim]
 * * 0x08 (RO) (OLD: 0x04) : Current Seconds (32-bit Raw Hardware Value)
 * * 0x10 (WO) (OLD: 0x08) : Time Holding Register (Value to be loaded into Seconds)
 * * 0x18 (RO) (OLD: 0x0C) : Current Epoch (16-bit hardware overflow counter)
 * * 0x20 (WO) (OLD: 0x10) : Epoch Holding Register (Value to be loaded into Epoch)
 * * 0x28 (RW) (OLD: 0x14) : Alarm Target Register (32-bit Seconds match value)
 * * 0x30 (RO) (OLD: 0x18) : Alarm Debug (Direct visibility into comparator state)
 * ==================================================================================
 */

package rtc_v1;
    
    `include "rtc.defines"
    `include "Logger.bsv"

    // Package Imports
    import Clocks::*;
    import Semi_FIFOF::*;       
    import AXI4_Lite_Types::*;  
    import AXI4_Types::*;       
    import GetPut::*;
    import FIFOF::*;
    import Connectable::*;
    import Vector::*;
    import BuildVector::*;      

    export Ifc_rtc_axi4lite   (..);     
    export Ifc_rtc_axi4       (..);
    export mkrtc_axi4;
    export mkrtc_axi4lite;
    export RTCIO              (..);
    
    export User_ifc           (..);
    export mkrtc;

    (*always_ready, always_enabled*)
    interface RTCIO;
        method Bit#(1) rtc_clock_signal;    // 1hz clock output (waveform)
    endinterface: RTCIO

    interface User_ifc#(numeric type addr_width, numeric type data_width);
        method ActionValue#(Bool) write_req(Bit#(addr_width) addr, Bit#(data_width) data);
        method ActionValue#(Tuple2#(Bool, Bit#(data_width))) read_req(Bit#(addr_width) addr);
        interface RTCIO io;
        method Bit#(1) sb_interrupt_to_plic;
    endinterface: User_ifc

    // -----------------------------------------------------------------
    // STRUCT DEFINITIONS
    // -----------------------------------------------------------------

    // Control Register (Bus Domain Storage)
    typedef struct {
        Bit#(16) prescl_trim;       // [31:16] - Prescaler Trim
        Bit#(9) reserved;           //  [15:7] - Unused
        Bit#(1)  intr_polarity;     //     [6] - Interrupt Polarity
        Bool     prescl_upd;        //     [5] - Update Trim
        Bool     alarm_clr;         //     [4] - Clear Interrupt
        Bool     alarm_en;          //     [3] - Enable Alarm
        Bool     load_en;           //     [2] - Load Time/Epoch
        Bool     cnt_en;            //     [1] - Start RTC
        Bool     cnt_rst;           //     [0] - Reset RTC
    } ControlReg deriving(Bits, Eq);

    // Internal Struct for Acknowledge Signals (Slow -> Fast)
    typedef struct {
        Bool     prescl_upd_ack;
        Bool     alarm_clr_ack;  
        Bool     load_en_ack;    
        Bool     cnt_rst_ack;    
    } InternalAcks deriving(Bits, Eq);

    typedef struct {
        Bit#(32) counter;
        Bit#(16) epoch;
        Bit#(32) alarm;
    } RtcDataIn deriving(Bits, Eq);

    typedef struct {
        Bit#(32) counter;
        Bit#(16) epoch;
        Bit#(32) alarm; // Optionally included alarm value in readback for debugging - can be removed if not needed
        Bit#(1)  irq;
    } RtcDataOut deriving(Bits, Eq);


    // MODULE IMPLEMENTATION
    (* descending_urgency = "rl_1hz_oscillate, rl_count_logic" *)
    module mkrtc#(Clock ext_clk) (User_ifc#(addr_width, data_width))
    provisos(
        Add#(a__, 32, data_width),
        Add#(c__, 4, data_width),
        Add#(d__, 16, data_width),
        Add#(e__, 8, addr_width),
        Add#(f__, addr_width, 32)
    );  
        // Create a reset for the Oscillator Domain, derived from the default System Reset
        Reset osc_rst <- mkAsyncResetFromCR(0, ext_clk);

        // Default Values
        let default_CtrlIn  = ControlReg{prescl_trim: 16383, reserved: 0, intr_polarity: 0, prescl_upd: False, alarm_clr: False, alarm_en: False, load_en: False, cnt_en: False, cnt_rst: False};
        let default_DataIn  = RtcDataIn{counter: 0, epoch: 0, alarm: 0};
        let default_DataOut = RtcDataOut{counter: 0, epoch: 0, irq: 0, alarm: 0}; // Can remove alarm from output if not needed for debugging
        let default_Acks    = InternalAcks{prescl_upd_ack: False, alarm_clr_ack: False, load_en_ack: False, cnt_rst_ack: False};

        // 1. FAST DOMAIN REGISTERS
        // These act as the "Mailbox". If a bit is 1, it means "Request Pending".
        Reg#(ControlReg) rg_ctrl_in    <- mkReg(default_CtrlIn); 
        Reg#(RtcDataIn)  rg_data_in    <- mkReg(default_DataIn);

        // PulseWire: Used to prevent the auto-clear logic from overriding a user write in the same cycle.
        PulseWire pw_user_write_ctrl     <- mkPulseWire;


        // 2. OSCILLATOR DOMAIN (CDC: Fast -> Ext)
        Reg#(ControlReg) sync_ctrl_osc  <- mkSyncRegFromCC(default_CtrlIn, ext_clk); 

        rule rl_ctrl_fast_to_osc;
            sync_ctrl_osc <= rg_ctrl_in;
        endrule

        // --- OSCILLATOR LOGIC ---
        Reg#(Bit#(16)) rg_pre_counter     <- mkRegA(0, clocked_by ext_clk, reset_by osc_rst);
        Reg#(Bit#(1))  rg_osc_state       <- mkRegA(0, clocked_by ext_clk, reset_by osc_rst);    
        
        // We keep clk_1hz for the RTCIO waveform, but we don't use it for logic.
        MakeClockIfc#(Bit#(1)) clk_1hz    <- mkClock(0, True, clocked_by ext_clk, reset_by osc_rst);

        Reg#(Bit#(16)) rg_active_trim      <- mkRegA(16383, clocked_by ext_clk, reset_by osc_rst);
        Reg#(Bool)     rg_prev_trim_update <- mkRegA(False, clocked_by ext_clk, reset_by osc_rst);
        
        // OSC ACK: Sends confirmation back to Fast domain
        Reg#(Bool)     rg_ack_prescl       <- mkSyncRegToCC(False, ext_clk, osc_rst);
        Reg#(Bool)     rg_ack_prescl_local <- mkRegA(False, clocked_by ext_clk, reset_by osc_rst);


        rule rl_1hz_oscillate;
            // Prescaler Update Logic
            if (sync_ctrl_osc.prescl_upd && !rg_prev_trim_update) begin
                rg_active_trim <= sync_ctrl_osc.prescl_trim;
            end
            rg_prev_trim_update <= sync_ctrl_osc.prescl_upd;
            
            // Mailbox Acknowledge: Mirror the request bit back
            if (sync_ctrl_osc.prescl_upd != rg_ack_prescl_local) begin  // making sure to only update the sync reg when there's a change, to avoid unnecessary toggling
                rg_ack_prescl       <= sync_ctrl_osc.prescl_upd;
                rg_ack_prescl_local <= sync_ctrl_osc.prescl_upd;
            end

            // Clock Generation
            Bit#(16) threshold = (rg_active_trim == 0) ? 16383 : rg_active_trim;
            if (sync_ctrl_osc.cnt_en) begin
                if (rg_pre_counter >= threshold) begin
                    rg_pre_counter <= 0;
                    let new_osc = ~rg_osc_state;
                    rg_osc_state <= new_osc;
                    clk_1hz.setClockValue(new_osc); 
                end else begin
                    rg_pre_counter <= rg_pre_counter + 1;
                    clk_1hz.setClockValue(rg_osc_state); 
                end
            end else begin
                clk_1hz.setClockValue(rg_osc_state);
            end
        endrule


        // The following block is for exporting a SYNCED 1hz clock through RTCIO
        // If your 1hz clock signal needs to interact with the Soc, CPU or Bus,
        // Sync this signal to the bus clock. If not, export the raw signal through RTCIO

        // SyncBitIfc#(Bit#(1)) sync_osc_state <- mkSyncBitToCC(ext_clk, osc_rst);

        // rule syncbits_out; // constantly send 1hz clock signal out
        //     sync_osc_state.send(rg_osc_state); 
        // endrule


        // 3. SLOW LOGIC DOMAIN (32.768 Hz) (CDC: Fast -> Slow)

        Reg#(ControlReg) sync_ctrl_slow <- mkSyncRegFromCC(default_CtrlIn, ext_clk);
        Reg#(Bit#(32)) rg_alarm_val_slow  <- mkSyncRegFromCC(0, ext_clk);
        Reg#(Bit#(32)) rg_load_time_slow  <- mkSyncRegFromCC(0, ext_clk);
        Reg#(Bit#(16)) rg_load_epoch_slow <- mkSyncRegFromCC(0, ext_clk);

        rule rl_ctrl_data_fast_to_slow;
            sync_ctrl_slow      <= rg_ctrl_in;
            rg_alarm_val_slow   <= rg_data_in.alarm;
            rg_load_time_slow   <= rg_data_in.counter;
            rg_load_epoch_slow  <= rg_data_in.epoch;
        endrule

        // --- COUNTER LOGIC ---
        Reg#(Bit#(32)) rg_1hz_counter <- mkRegA(0, clocked_by ext_clk, reset_by osc_rst);
        Reg#(Bit#(16)) rg_epoch       <- mkRegA(0, clocked_by ext_clk, reset_by osc_rst);
        Reg#(Bit#(1))  rg_irq         <- mkRegA(0, clocked_by ext_clk, reset_by osc_rst);
        
        // Previous State Detectors 
        Reg#(Bool) rg_prev_load_en   <- mkRegA(False, clocked_by ext_clk, reset_by osc_rst);
        Reg#(Bool) rg_prev_cnt_rst   <- mkRegA(False, clocked_by ext_clk, reset_by osc_rst);
        Reg#(Bool) rg_prev_alarm_clr <- mkRegA(False, clocked_by ext_clk, reset_by osc_rst);
        
        // SLOW ACKS: Send confirmation back to Fast domain
        // We pack all slow ACKs into one struct
        Reg#(InternalAcks) rg_slow_acks_sync  <- mkSyncRegToCC(default_Acks, ext_clk, osc_rst);
        Reg#(InternalAcks) rg_slow_acks_local <- mkRegA(default_Acks, clocked_by ext_clk, reset_by osc_rst);

        // This signal indicates that a tick has occurred and the counting logic needs to update the time.
        Reg#(Bit#(1)) rg_osc_state_prev <- mkRegA(0, clocked_by ext_clk, reset_by osc_rst);

        rule rl_count_logic;
            // Track the previous state every cycle
            rg_osc_state_prev <= rg_osc_state;
            // Detect the rising edge (1Hz Tick)
            Bool is_tick = (rg_osc_state == 1 && rg_osc_state_prev == 0);

            if (!sync_ctrl_slow.load_en) rg_prev_load_en   <= False;
            if (!sync_ctrl_slow.cnt_rst) rg_prev_cnt_rst   <= False;
            if (!sync_ctrl_slow.alarm_clr)  rg_prev_alarm_clr <= False;

            // Mailbox Acknowledge: Mirror the request bits back (level sensitive ACK)
            let current_acks = default_Acks;
            current_acks.load_en_ack   = sync_ctrl_slow.load_en;
            current_acks.cnt_rst_ack   = sync_ctrl_slow.cnt_rst;
            current_acks.alarm_clr_ack = sync_ctrl_slow.alarm_clr;
            
            if (current_acks != rg_slow_acks_local) begin       //making sure to only update the sync reg when there's a change, to avoid unnecessary toggling
                rg_slow_acks_sync <= current_acks;
                rg_slow_acks_local <= current_acks;
            end

            Bit#(32) next_time  = rg_1hz_counter;
            Bit#(16) next_epoch = rg_epoch;
            Bool set_happened   = False;

            // 1. Reset
            if (sync_ctrl_slow.cnt_rst && !rg_prev_cnt_rst) begin
                next_time  = 0; // Use '=' for local variables
                next_epoch = 0;
                rg_prev_cnt_rst <= True; 
                set_happened = True;
            end
            // 2. Load
            else if (sync_ctrl_slow.load_en && !rg_prev_load_en) begin
                next_time  = rg_load_time_slow;
                next_epoch = rg_load_epoch_slow;
                rg_prev_load_en <= True;
                set_happened = True;
            end 

            // 3. Count (Now triggered by the sticky bit rg_tick_pending)
            if (!set_happened && sync_ctrl_slow.cnt_en && is_tick) begin
                if (rg_1hz_counter == 32'hFFFFFFFF) begin
                    next_time  = 0;
                    next_epoch = next_epoch + 1; 
                end else begin
                    next_time = next_time + 1;
                end
            end

            rg_1hz_counter <= next_time;
            rg_epoch       <= next_epoch;
            
            // 4. Alarm
            if (sync_ctrl_slow.alarm_clr && !rg_prev_alarm_clr) begin
                rg_irq <= 0;
                rg_prev_alarm_clr <= True;
            end 
            else if ((next_time == rg_alarm_val_slow) && sync_ctrl_slow.alarm_en) begin
                rg_irq <= 1;
            end

        endrule


        // 4. FEEDBACK (Slow -> Fast) & HANDSHAKE
        Reg#(RtcDataOut) rg_sync_data_out <- mkSyncRegToCC(default_DataOut, ext_clk, osc_rst);
        Reg#(RtcDataOut) rg_sync_out_local <- mkRegA(default_DataOut, clocked_by ext_clk, reset_by osc_rst);
        
        rule rl_sync_feedback; // Runs in Slow Domain
            let current_out = RtcDataOut{counter: rg_1hz_counter, epoch: rg_epoch, irq: rg_irq, alarm: rg_alarm_val_slow};
            // Only push to the fast domain if data actually changed
            if (current_out != rg_sync_out_local) begin
                rg_sync_data_out  <= current_out;
                rg_sync_out_local <= current_out;
            end
        endrule

        // === MAILBOX CLEARING RULE (Runs in Fast Domain) ===
        // If (Request == 1 AND Ack == 1) -> Set Request = 0.
        rule rl_handshake_clearing (!pw_user_write_ctrl);
            let ctrl = rg_ctrl_in;
            let slow_acks = rg_slow_acks_sync; // Read synced ACKs

            // 1. Oscillator Handshake
            if (ctrl.prescl_upd && rg_ack_prescl) begin
                ctrl.prescl_upd = False; // Clear the mailbox
                //$display("RTC: [Handshake] Prescaler Update Complete. Clearing Bit.");
            end

            // 2. Slow Logic Handshakes
            if (ctrl.load_en && slow_acks.load_en_ack) begin
                ctrl.load_en = False;
                //$display("RTC: [Handshake] Load Time Complete. Clearing Bit.");
            end
            
            if (ctrl.cnt_rst && slow_acks.cnt_rst_ack) begin
                ctrl.cnt_rst = False;
                //$display("RTC: [Handshake] Counter Reset Complete. Clearing Bit.");
            end

            if (ctrl.alarm_clr && slow_acks.alarm_clr_ack) begin
                ctrl.alarm_clr = False;
                //$display("RTC: [Handshake] Alarm Clear Complete. Clearing Bit.");
            end

            rg_ctrl_in <= ctrl;
        endrule

        // Helper to calculate the final polarized interrupt state
        function Bit#(1) get_final_interrupt();
            // Polarity: 1=Active Low (invert), 0=Active High (stay same)
            return (rg_ctrl_in.intr_polarity == 1) ? ~rg_sync_data_out.irq : rg_sync_data_out.irq;
        endfunction


        // 5. INTERFACE METHODS
        method ActionValue#(Tuple2#(Bool, Bit#(data_width))) read_req(Bit#(addr_width) addr);
            Bit#(data_width) temp = 0;
            Bool success = True;
            Bit#(8) offset = addr[7:0];

            if (offset == `RTC_CTRL_ADDR) begin     
                // READBACK BEHAVIOR:
                // We return 'rg_ctrl_in'. If a bit is 1, it means the hardware is still busy
                // processing that command. If the bit is 0, the Handshake rule has cleared it.
                temp = zeroExtend(pack(rg_ctrl_in));
            end
            else if (offset == `RTC_TIME_READ_ADDR)    temp = zeroExtend(rg_sync_data_out.counter); 
            else if (offset == `RTC_EPOCH_READ_ADDR)   temp = zeroExtend(rg_sync_data_out.epoch); 
            else if (offset == `RTC_TIME_WRITE_ADDR)   temp = zeroExtend(rg_data_in.counter); 
            else if (offset == `RTC_EPOCH_WRITE_ADDR)  temp = zeroExtend(rg_data_in.epoch);
            else if (offset == `RTC_ALARM_ADDR)        temp = zeroExtend(rg_data_in.alarm);
            else if (offset == `RTC_ALARM_READ)        temp = zeroExtend(rg_sync_data_out.alarm); //TODO
            else success = False;

            return tuple2(success, temp);
        endmethod

        method ActionValue#(Bool) write_req(Bit#(addr_width) addr, Bit#(data_width) data);
            Bool success = True;
            Bit#(8) offset = truncate(addr); 

            if (offset == `RTC_CTRL_ADDR) begin
                ControlReg new_ctrl = unpack(truncate(data));
                //$display("RTC: Writing to Control: %h", data);
                pw_user_write_ctrl.send();
                rg_ctrl_in <= new_ctrl;
            end
            else if (offset == `RTC_TIME_WRITE_ADDR) begin
                //$display("RTC: Writing to TIME Holding Reg: %h", data); 
                RtcDataIn d = rg_data_in;
                d.counter = truncate(data);
                rg_data_in <= d;
            end
            else if (offset == `RTC_EPOCH_WRITE_ADDR) begin
                //$display("RTC: Writing to EPOCH Holding Reg: %h", data);
                RtcDataIn d = rg_data_in;
                d.epoch = data[15:0];
                rg_data_in <= d;
            end
            else if (offset == `RTC_ALARM_ADDR) begin       // Can be removed if we want to make it write-only without readback          
                //$display("RTC: Writing to ALARM Holding Reg: %h", data); 
                RtcDataIn d = rg_data_in;
                d.alarm = truncate(data);
                rg_data_in <= d;
                //rg_data_in <= RtcDataIn{counter: rg_data_in.counter, epoch: rg_data_in.epoch, alarm: truncate(data)};
            end
            else begin
                success = False;
            end
            return success;
        endmethod

        interface RTCIO io;
            method Bit#(1) rtc_clock_signal;        
                return rg_osc_state;   // if sync to bus_clk needed, use sync_osc_state.read instead of rg_osc_state (check comment above)
            endmethod
        endinterface

        method Bit#(1) sb_interrupt_to_plic;
                return get_final_interrupt(); 
        endmethod
    endmodule: mkrtc


    // AXI4-Lite Wrapper Logic 
    interface Ifc_rtc_axi4lite#(numeric type addr_width, numeric type data_width, numeric type user_width);
        interface AXI4_Lite_Slave_IFC#(addr_width, data_width, user_width) slave;
        (*always_ready, always_enabled*)
        interface RTCIO io;
        method Bit#(1) rtc_sb_interrupt;
    endinterface

    module mkrtc_axi4lite#(Clock ext_clk)(Ifc_rtc_axi4lite#(addr_width, data_width, user_width))
    provisos(
        Add#(a__, 32, data_width),
        Add#(c__, 4, data_width),
        Add#(d__, 16, data_width),
        Add#(e__, 8, addr_width),
        Add#(f__, addr_width, 32)
    );    
        User_ifc#(addr_width, data_width) rtc <- mkrtc(ext_clk);
        AXI4_Lite_Slave_Xactor_IFC#(addr_width, data_width, user_width) s_xactor <- mkAXI4_Lite_Slave_Xactor();

        rule rl_write_request;
            let addreq <- pop_o(s_xactor.o_wr_addr);
            let datareq <- pop_o(s_xactor.o_wr_data);
            let succ <- rtc.write_req(addreq.awaddr, datareq.wdata);
            let resp = AXI4_Lite_Wr_Resp {bresp: succ ? AXI4_LITE_OKAY : AXI4_LITE_SLVERR, buser: addreq.awuser};
            s_xactor.i_wr_resp.enq(resp);            
        endrule

        rule rl_read_request;
            let req <- pop_o(s_xactor.o_rd_addr);
            let {succ, data} <- rtc.read_req(req.araddr);
            let resp = AXI4_Lite_Rd_Data {rresp: succ ? AXI4_LITE_OKAY : AXI4_LITE_SLVERR, rdata: data, ruser: ?};
            s_xactor.i_rd_data.enq(resp);
        endrule

        interface slave = s_xactor.axi_side;
        interface io =  interface RTCIO
                            method Bit#(1) rtc_clock_signal; return rtc.io.rtc_clock_signal; endmethod
                        endinterface;
        method Bit#(1) rtc_sb_interrupt; return rtc.sb_interrupt_to_plic; endmethod
    endmodule

    // AXI4 Wrapper Logic Here 
    interface Ifc_rtc_axi4#(numeric type addr_width, numeric type id_width, numeric type data_width, numeric type user_width);
        interface AXI4_Slave_IFC#(addr_width, id_width, data_width, user_width) slave;
        (*always_ready, always_enabled*)
        interface RTCIO io;
        method Bit#(1) rtc_sb_interrupt;
    endinterface

    module mkrtc_axi4#(Clock ext_clk)(Ifc_rtc_axi4#(addr_width, id_width, data_width, user_width))
    provisos(
        Add#(a__, 32, data_width),
        Add#(c__, 4, data_width),
        Add#(d__, 16, data_width),
        Add#(e__, 8, addr_width),
        Add#(f__, addr_width, 32)
    );  
        User_ifc#(addr_width, data_width) rtc <- mkrtc(ext_clk);
        AXI4_Slave_Xactor_IFC#(addr_width, id_width, data_width, user_width) s_xactor <- mkAXI4_Slave_Xactor();
        
        Reg#(Bit#(8)) rg_wrburst_count <- mkRegA(0);
        Reg#(Bit#(8)) rg_rdburst_count <- mkRegA(0);
        Reg#(AXI4_Wr_Addr#(addr_width, id_width, user_width)) rg_wrpacket <- mkRegA(?);   
        Reg#(AXI4_Rd_Addr#(addr_width, id_width, user_width)) rg_rdpacket <- mkRegA(?);
        
        rule rl_write_request (rg_wrburst_count == 0);
            let addreq <- pop_o(s_xactor.o_wr_addr);
            let datareq <- pop_o(s_xactor.o_wr_data);
            let succ <- rtc.write_req(addreq.awaddr, datareq.wdata);
            if (addreq.awlen != 0) begin
                rg_wrpacket <= addreq; 
                rg_wrburst_count <= 1; 
            end else begin
                let resp = AXI4_Wr_Resp {bresp: succ ? AXI4_OKAY : AXI4_SLVERR, buser: addreq.awuser, bid: addreq.awid};
                s_xactor.i_wr_resp.enq(resp);
            end
        endrule

        rule rl_write_burst_drain (rg_wrburst_count != 0);
            let datareq <- pop_o(s_xactor.o_wr_data);
            let addreq = rg_wrpacket;
            if (datareq.wlast) begin
                let resp = AXI4_Wr_Resp {bresp: AXI4_SLVERR, buser: addreq.awuser, bid: addreq.awid};
                s_xactor.i_wr_resp.enq(resp);
                rg_wrburst_count <= 0;
            end
        endrule

        rule rl_read_request (rg_rdburst_count == 0);
            let req <- pop_o(s_xactor.o_rd_addr);
            let {succ, data} <- rtc.read_req(req.araddr);
            let resp = AXI4_Rd_Data {rresp: succ ? AXI4_OKAY : AXI4_SLVERR, rid: req.arid, rlast: (req.arlen == 0), rdata: data, ruser: ?};
            s_xactor.i_rd_data.enq(resp);
            if (req.arlen != 0) begin
                rg_rdpacket <= req;
                rg_rdburst_count <= 1; 
            end
        endrule

        rule rl_read_burst_error (rg_rdburst_count != 0);
            let req = rg_rdpacket;
            Bool is_last = (rg_rdburst_count == req.arlen);
            let resp = AXI4_Rd_Data {rresp: AXI4_SLVERR, rid: req.arid, rlast: is_last, rdata: 0, ruser: ?};
            s_xactor.i_rd_data.enq(resp);
            if (is_last) rg_rdburst_count <= 0;
            else         rg_rdburst_count <= rg_rdburst_count + 1;
        endrule

        interface slave = s_xactor.axi_side;
        interface io =  interface RTCIO
                            method Bit#(1) rtc_clock_signal; return rtc.io.rtc_clock_signal; endmethod
                        endinterface;
        method Bit#(1) rtc_sb_interrupt; return rtc.sb_interrupt_to_plic; endmethod
    endmodule
    
endpackage: rtc_v1