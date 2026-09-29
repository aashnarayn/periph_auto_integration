#include "util.h"
#include "watchdog_driver.h"

int main(){
  printf("\nHello\n");
  set64(WD_Counter,15000);
  set64(WD_Control,(WDT_Reset_Mode | WDT_Start));
  while(1) {
    printf("What\n");
  }
  return 0;
}

