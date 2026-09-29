//Memory Maps                                                                                       
#define WD_Counter      0x30000
#define WD_Control      0x30008
#define WD_Reset_Cycles 0x30010
#define WD_Active       0x30018

//System functions
void set64(uint64_t* addr, uint64_t data)
{
  *addr=data;
}


#define WDT_Active()        set64(WD_Active,1)

//Control Register Bits
//Bit 0 WDT Start/Stop
#define WDT_Start         1<<0
#define WDT_Stop          0<<0

//Bit 1 WDT Mode
#define WDT_Interrupt_Mode  0<<1
#define WDT_Reset_Mode      1<<1

//Bit 2 Soft Reset SoC
#define WDT_Soft_Reset()    1<<2

