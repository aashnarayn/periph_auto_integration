## 			Makefile for C-Class on Arty-A7
-include makefile.inc
XLEN_build ?= 64
BOARD ?= nexys_video
SYNTHTOP ?= fpga_top
ISA=RV$(XLEN_build)IMAFDCSU
JOBS:=$(shell nproc)
buswidth := $(word 2,$(subst =, ,$(filter buswidth=%,$(BSC_DEFINES))))
SHAKTI_HOME:=$(CURDIR)

WORKING_DIR := $(shell pwd)
Vivado_loc=$(shell which vivado || which vivado_lab)

SKIP_DRY_RUN ?= 0

ifeq ($(BSCAN2E), enable)
	override BSC_DEFINES += bscan2e=True
	JTAG_TYPE:=JTAG_BSCAN2E
else
	JTAG_TYPE:=JTAG_EXTERNAL
endif


ifeq ($(BOARD), arty_a7)
	FPGA:=xc7a100tcsg324-1
	MCS:=true
else ifeq ($(BOARD), arty_a7_yamuna)
	FPGA:=xc7a100tcsg324-1
	MCS:=true
else ifeq ($(BOARD), nexys_a7)
	FPGA:=xc7a100tcsg324-1
	MCS:=true
else ifeq ($(BOARD), arty_a7_ganga)
	FPGA:=xc7a100tcsg324-1
	MCS:=true
else ifeq ($(BOARD), nexys4_ddr)
	FPGA:=xc7a100tcsg324-1
	MCS:=true
else ifeq ($(BOARD), arty_a7_fpu)
	FPGA:=xc7a100tcsg324-1
	MCS:=true
else ifeq ($(BOARD), profpga)
	FPGA:=xcvu440-flga2892-1-c
	MCS:=false
else ifeq ($(BOARD), nexys_video)
	FPGA:=xc7a200tsbg484-1
	MCS:=true
else ifeq ($(BOARD), vcu118)
	FPGA:=xcvu9p-flga2104-2L-e
	MCS:=false
else ifeq ($(BOARD), vcu108)
	FPGA:=xcvu095-ffva2104-2-e
	MCS:=false
else ifeq ($(BOARD), ac701)
	FPGA:=xc7a200tfbg676-2
	MCS:=true
else ifeq ($(BOARD), zcu106)
	FPGA:=xczu7ev-ffvc1156-2-e
	MCS:=false
else ifeq ($(BOARD), kcu105)
	FPGA:=xcku040-ffva1156-2-e
	MCS:=false
endif

ifeq ($(buswidth), 128)
  BUS_WIDTH:=BUS_WIDTH128
else ifeq ($(buswidth), 64)
  BUS_WIDTH:=BUS_WIDTH64
else ifeq ($(buswidth), 32)
  BUS_WIDTH:=BUS_WIDTH32
endif


MOREDEFINES=$(addprefix -D , $(BSC_DEFINES))

# ------------------------------------- Makefile TARGETS ----------------------------------------- #
default: quick_build_xilinx
build:  quick_build
build_sim: quick_build_sim
build_sim_gdb: quick_build_sim_gdb
build_vcs_sim: gen_sim_vcs_files
build_vcs_sim_gdb: gen_sim_vcs_gdb_files
yamuna_asic: quick_asic_build
build_hyp: quick_build_xilinx_hyp
build: ## To start bitstream and MCS file generation for boards (passed through BOARD parameter)

rebuild:
	@make generate_verilog -j && make generate_boot_files ip_build board_build

build_program: quick_build_xilinx program_mcs
build_program: ## To start bitstream and MCS file generation  for boards (passed through BOARD parameter) and program it to the BOARD connected.

%.bo:
	 $(info building $@)
	 $(info BSC_DEFINES = $(MOREDEFINES))
	 @$(BSCCMD) $(MOREDEFINES) -p $(BSVINCDIR) $<

.PHONY: help
help: ## This help dialog.
	@fgrep -h "##" $(MAKEFILE_LIST) | fgrep -v fgrep | sed -e 's/\\$$//' | sed -e 's/##//' | column	-c2 -t -s :

.PHONY: run_setup_fpga
run_setup_fpga: ## Run command to setup all dependant files for FPGA.
	@pip3 install -r requirements.txt
	@cp -r boards/$(BOARD)/. .
	@python3 -m configure.main --ispec sample_config/c$(XLEN_build)_$(BOARD)/rv$(XLEN_build)i_isa.yaml \
	--customspec sample_config/c$(XLEN_build)_$(BOARD)/rv$(XLEN_build)i_custom.yaml \
	--cspec sample_config/c$(XLEN_build)_$(BOARD)/core$(XLEN_build).yaml \
	--gspec sample_config/c$(XLEN_build)_$(BOARD)/csr_grouping$(XLEN_build).yaml \
	--dspec sample_config/c$(XLEN_build)_$(BOARD)/rv$(XLEN_build)i_debug.yaml
# ifeq ($(BOARD), arty_a7)
# endif
	@mkdir build
	@mkdir build/hw
	@mkdir build/hw/intermediate
	@mkdir build/hw/verilog

.PHONY: run_setup_build
run_setup_build: ## Run command to setup all dependant files for FPGA.
	@pip3 uninstall -r requirements.txt -y
	@pip uninstall soc_config -y
	@pip install -r requirements.txt
	@cp -r boards/$(BOARD)/. .
	@repomanager --yaml deps.yaml --clean 
	@repomanager --yaml deps.yaml -cup
	@soc_config --ispec sample_config/c$(XLEN_build)_$(BOARD)/rv$(XLEN_build)i_isa.yaml \
	--customspec sample_config/c$(XLEN_build)_$(BOARD)/rv$(XLEN_build)i_custom.yaml \
	--cspec sample_config/c$(XLEN_build)_$(BOARD)/core$(XLEN_build).yaml \
	--gspec sample_config/c$(XLEN_build)_$(BOARD)/csr_grouping$(XLEN_build).yaml \
	--dspec sample_config/c$(XLEN_build)_$(BOARD)/rv$(XLEN_build)i_debug.yaml
# ifeq ($(BOARD), arty_a7)
# endif
	@mkdir build
	@mkdir build/hw
	@mkdir build/hw/intermediate
	@mkdir build/hw/verilog

.PHONY: run_setup_simulate
run_setup_simulate: ## Run command to setup all dependant files for FPGA.
	@pip3 uninstall -r requirements.txt -y
	@pip uninstall soc_config -y
	@pip install -r requirements.txt
	@cp -r boards/$(BOARD)/. .
	@repomanager --yaml deps.yaml --clean 
	@repomanager --yaml deps.yaml -cup
	@soc_config --ispec sample_config/c$(XLEN_build)_$(BOARD)_sim/rv$(XLEN_build)i_isa.yaml \
	--customspec sample_config/c$(XLEN_build)_$(BOARD)_sim/rv$(XLEN_build)i_custom.yaml \
	--cspec sample_config/c$(XLEN_build)_$(BOARD)_sim/core$(XLEN_build).yaml \
	--gspec sample_config/c$(XLEN_build)_$(BOARD)_sim/csr_grouping$(XLEN_build).yaml \
	--dspec sample_config/c$(XLEN_build)_$(BOARD)_sim/rv$(XLEN_build)i_debug.yaml
# ifeq ($(BOARD), arty_a7)
# endif
	@mkdir build
	@mkdir build/hw
	@mkdir build/hw/intermediate
	@mkdir build/hw/verilog

.PHONY: run_setup_simulate_gdb
run_setup_simulate_gdb: ## Run command to setup all dependant files for FPGA.
	@pip3 uninstall -r requirements.txt -y
	@pip uninstall soc_config -y
	@pip install -r requirements.txt
	@cp -r boards/$(BOARD)/. .
	@repomanager --yaml deps.yaml --clean 
	@repomanager --yaml deps.yaml -cup
	@soc_config --ispec sample_config/c$(XLEN_build)_$(BOARD)_sim_gdb/rv$(XLEN_build)i_isa.yaml \
	--customspec sample_config/c$(XLEN_build)_$(BOARD)_sim_gdb/rv$(XLEN_build)i_custom.yaml \
	--cspec sample_config/c$(XLEN_build)_$(BOARD)_sim_gdb/core$(XLEN_build).yaml \
	--gspec sample_config/c$(XLEN_build)_$(BOARD)_sim_gdb/csr_grouping$(XLEN_build).yaml \
	--dspec sample_config/c$(XLEN_build)_$(BOARD)_sim_gdb/rv$(XLEN_build)i_debug.yaml
# ifeq ($(BOARD), arty_a7)
# endif
	@mkdir build
	@mkdir build/hw
	@mkdir build/hw/intermediate
	@mkdir build/hw/verilog

.PHONY: run_setup_fpga_hyp
run_setup_fpga_hyp: ## Run command to setup all dependant files for FPGA.
	@pip3 install -r requirements.txt
	@python3 -m configure.main --ispec sample_config/h64_$(BOARD)/rv64i_isa.yaml \
	--customspec sample_config/h64_$(BOARD)/rv64i_custom.yaml \
	--cspec sample_config/h64_$(BOARD)/core64.yaml \
	--gspec sample_config/h64_$(BOARD)/csr_grouping64.yaml \
	--dspec sample_config/h64_$(BOARD)/rv64i_debug.yaml
# ifeq ($(BOARD), arty_a7)
	@cp -r boards/$(BOARD)/. .
# endif
	@mkdir build
	@mkdir build/hw
	@mkdir build/hw/intermediate
	@mkdir build/hw/verilog

	
.PHONY: run_setup_sim
run_setup_sim: ## Run command to setup all dependant files for simulation.
	@pip3 install -r requirements.txt
	./run_sim.sh

.PHONY: simulate
simulate: ## Compile BSV and generate simulation executable with Verilator and boot files
simulate: run_setup_sim
	@make generate_verilog -j && make generate_boot_files_sim link_verilator 

.PHONY: generate_boot_files
generate_boot_files:
ifeq ($(XLEN_build), 32)
	@cd boot-code-yamuna; make
	@cp boot-code-yamuna/outputs/boot.mem $(VERILOGDIR)/
else ifeq ($(XLEN_build), 64)
	@cd boot-code-ganga; make
	@cp boot-code-ganga/outputs/boot.mem $(VERILOGDIR)/
else
	@cd boot-code; make
	@cp boot-code/outputs/boot.mem $(VERILOGDIR)/
endif

.PHONY: quick_build
quick_build: run_setup_build
	@make generate_verilog -j && make generate_boot_files ip_build board_build

.PHONY: quick_build_sim
quick_build_sim: run_setup_simulate
	@make generate_verilog -j && make generate_boot_files_sim link_verilator

.PHONY: quick_build_sim_gdb
quick_build_sim_gdb: run_setup_simulate_gdb
	@make generate_verilog -j && make generate_boot_files_sim link_verilator_gdb

##For verification vcs setup
.PHONY: gen_sim_vcs_files
gen_sim_vcs_files: ## Compile BSV and generate simulation executable with VCS and boot files
gen_sim_vcs_files: run_setup_simulate
	@make generate_verilog -j && make link_vcs -j && make generate_boot_files_sim

##For VCS GDB
.PHONY: gen_sim_vcs_gdb_files
gen_sim_vcs_gdb_files: ## Compile BSV and generate simulation executable with VCS and boot files
gen_sim_vcs_gdb_files: run_setup_simulate_gdb
	@make generate_verilog -j && make link_vcs_gdb -j && make generate_boot_files_sim
	

.PHONY: quick_asic_build
quick_asic_build: run_setup_fpga
	@make generate_verilog_asic -j

.PHONY: quick_build_xilinx_hyp
quick_build_xilinx_hyp: run_setup_fpga_hyp
	@make generate_verilog -j && make generate_boot_files ip_build board_build  

.PHONY: link_verilator                                                                              
link_verilator: ## Generate simulation executable using Verilator                                   
	@echo "Linking $(TOP_MODULE) using verilator"
	@mkdir -p $(BSVOUTDIR) obj_dir
	@echo "#define TOPMODULE V$(TOP_MODULE)" > sim_main.h                                             
	@echo '#include "V$(TOP_MODULE).h"' >> sim_main.h                                                 
	verilator $(VERILATOR_FLAGS) --cc $(TOP_MODULE).v -y $(VERILOGDIR) \
	-y $(BS_VERILOG_LIB) -y common_verilog --exe        
	@ln -f -s sim_main.cpp obj_dir/sim_main.cpp                                                       
	@ln -f -s sim_main.h obj_dir/sim_main.h                                                           
	@make $(VERILATOR_SPEED) VM_PARALLEL_BUILDS=1 -j8 -C obj_dir -f V$(TOP_MODULE).mk                                                         
	@cp obj_dir/V$(TOP_MODULE) $(BSVOUTDIR)/out

.PHONY: link_vcs                                                                             
link_vcs: ## Generate simulation executable using VCS                                   
	@echo "Linking $(TOP_MODULE) using VCS"
	@export LD_LIBRARY_PATH=${PWD}
	@mkdir -p $(BSVOUTDIR)
	@cp vcs_verilog/vcs_sim.v $(VERILOGDIR)
	@vcs -debug_access+all -fgp -full64 -v2005 -sverilog +vcs+lic+wait -LDFLAGS -Wl,--no-as-needed -l vcs_compile_chain_sdf.log +compsdf +sdfverbose \
	+define+verbose_1 +define+BSV_RESET_FIFO_ARRAY +define+BSV_RESET_FIFO_HEAD +define+BSV_ASYNC_RESET +vpi +v2k +incdir+${PWD}/build/hw/verilog/ +libext+.v $(VERILOGDIR)/*.v  -top vcs_sim +warn=noTFX-DUMPVARCA \
	-timescale=1ns/1ps +notimingcheck +cli+4 -o $(BSVOUTDIR)/out
	@export LD_LIBRARY_PATH=${PWD}/$(BSVOUTDIR)
	@echo Linking finished

.PHONY: link_verilator_gdb
link_verilator_gdb: ## Generate simulation executable using Verilator and VPI for GDB
	@echo "Linking Verilator With the Shakti RBB Vpi"
	@mkdir -p $(BSVOUTDIR) obj_dir
	@echo "#define TOPMODULE V$(TOP_MODULE)_edited" >sim_main.h
	@echo '#include "V$(TOP_MODULE)_edited.h"' >> sim_main.h
	@sed  -f devices/jtagdtm/sed_script.txt  $(VERILOGDIR)/$(TOP_MODULE).v > tmp1.v
	@cat  devices/jtagdtm/verilator_config.vlt \
	      devices/jtagdtm/vpi_sv.v \
	      tmp1.v                         > $(VERILOGDIR)/$(TOP_MODULE)_edited.v
	@rm   -f  tmp1.v
	verilator $(VERILATOR_FLAGS) --threads-dpi all --cc $(TOP_MODULE)_edited.v --exe sim_main.cpp devices/jtagdtm/RBB_Shakti.c -y $(VERILOGDIR) -y $(BS_VERILOG_LIB) -y common_verilog 
	@ln -f -s sim_main.cpp obj_dir/sim_main.cpp
	@ln -f -s sim_main.h obj_dir/sim_main.h
	@ln -f -s ./devices/jtagdtm/RBB_Shakti.c obj_dir/RBB_Shakti.c
	@echo "INFO: Linking verilated files"
	make $(VERILATOR_SPEED) VM_PARALLEL_BUILDS=1 -j4 -C obj_dir -f V$(TOP_MODULE)_edited.mk
	@cp obj_dir/V$(TOP_MODULE)_edited $(BSVOUTDIR)/out
	@cp gdb_setup/code.mem$(XLEN) $(BSVOUTDIR)/code.mem
	@echo Linking finished

.PHONY: link_vcs_gdb
link_vcs_gdb: ## Generate simulation executable using vcs for GDB
	@echo "Linking VCS With the Shakti RBB Vpi"
	@export LD_LIBRARY_PATH=${PWD}
	@mkdir -p $(BSVOUTDIR)
	@rm -rf  $(BSVOUTDIR)/*
	@cp vcs_verilog/vcs_sim.v $(VERILOGDIR)
	@vcs -debug_access+all -kdb -fgp -full64 -v2005 -sverilog +vcs+lic+wait -LDFLAGS -Wl,--no-as-needed -l vcs_compile_chain_sdf.log +compsdf +sdfverbose \
	+define+verbose_1 +define+BSV_RESET_FIFO_ARRAY +define+BSV_RESET_FIFO_HEAD +define+BSV_ASYNC_RESET +vpi +v2k +incdir+${PWD}/$(VERILOGDIR)  +libext+.v -load ./rbb_vpi.so  $(VERILOGDIR)/*.v -top vcs_sim  +warn=noTFX-DUMPVARCA \
	-timescale=1ns/1ps +notimingcheck +cli+4 -o $(BSVOUTDIR)/out
	@cp gdb_setup/code.mem$(XLEN) $(BSVOUTDIR)/code.mem
	@cp *.so  $(BSVOUTDIR)
	@export LD_LIBRARY_PATH=${PWD}/$(BSVOUTDIR)
	@echo Linking finished

.PHONY: generate_verilog 
generate_verilog: $(BSVBUILDDIR)/$(TOP_BIN)
	@cp ${BS_VERILOG_LIB}/../Verilog.Vivado/RegFile.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/../Verilog.Vivado/BRAM2BELoad.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/FIFO2.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/FIFO1.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/FIFO10.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/FIFOL1.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/Counter.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/SizedFIFO.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/ResetEither.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/MakeReset0.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/SyncReset0.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/ClockInverter.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/SyncFIFO1.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/SyncFIFO.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/RevertReg.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/SyncBit.v ${VERILOGDIR}
	@cp ./common_verilog/bram_1rw.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/FIFO20.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/BRAM1.v ${VERILOGDIR}
	@cp ./common_verilog/bram_2rw.v ${VERILOGDIR}
	@cp ./c-class/src/fpu/hardfloat/verilog_src/* ${VERILOGDIR}
	@cp common_verilog/signedmul.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/SyncRegister.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/MakeClock.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/UngatedClockMux.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/ClockMux.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/MakeResetA.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/SyncResetA.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/SyncHandshake.v ${VERILOGDIR}
	@echo Compilation finished
	
.PHONY: generate_verilog_asic 
generate_verilog_asic: $(BSVBUILDDIR)/$(TOP_BIN)
	@cp ${BS_VERILOG_LIB}/../Verilog.Vivado/RegFile.v ${VERILOGDIR}    
	@cp ${BS_VERILOG_LIB}/FIFO2.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/FIFO1.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/FIFO10.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/FIFOL1.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/Counter.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/SizedFIFO.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/ResetEither.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/MakeReset0.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/SyncReset0.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/ClockInverter.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/SyncFIFO1.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/SyncFIFO.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/RevertReg.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/SyncBit.v ${VERILOGDIR}
	@cp ./bram_verilog/bram_1rw.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/FIFO20.v ${VERILOGDIR}
	@cp ./bram_verilog/BRAM1.v ${VERILOGDIR}
	@cp common_verilog/signedmul.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/SyncRegister.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/MakeClock.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/UngatedClockMux.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/ClockMux.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/MakeResetA.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/SyncResetA.v ${VERILOGDIR}
	@cp ${BS_VERILOG_LIB}/SyncHandshake.v ${VERILOGDIR}
	@@rm -rf ${VERILOGDIR}/*.use
	@echo Compilation finished

.PHONY: regress
regress: ## To run regressions on the core.
	@SHAKTI_HOME=$$PWD perl -I$(SHAKTI_HOME)/verification/verif-scripts $(SHAKTI_HOME)/verification/verif-scripts/makeRegress.pl $(opts)

.PHONY: test
test: ## To run a single riscv-test on the core.
	@SHAKTI_HOME=$$PWD CONFIG_LOG=0 perl -I$(SHAKTI_HOME)/verification/verif-scripts $(SHAKTI_HOME)/verification/verif-scripts/makeTest.pl $(opts)

.PHONY: torture
torture: ## To run riscv-tortur on the core.
	@SHAKTI_HOME=$$PWD perl -I$(SHAKTI_HOME)/verification/verif-scripts $(SHAKTI_HOME)/verification/verif-scripts/makeTorture.pl $(opts)

.PHONY: aapg
aapg: ## to generate and run aapf tests
	@SHAKTI_HOME=$$PWD perl -I$(SHAKTI_HOME)/verification/verif-scripts $(SHAKTI_HOME)/verification/verif-scripts/makeAapg.pl $(opts)

.PHONY: csmith
csmith: ## to generate and run csmith tests
	@SHAKTI_HOME=$$PWD perl -I$(SHAKTI_HOME)/verification/verif-scripts $(SHAKTI_HOME)/verification/verif-scripts/makeCSmith.pl $(opts)

.PHONY: update_xlen
update_xlen:
	@echo "XLEN=$(XLEN)" > verification/dts/Makefile.inc

.PHONY: generate_boot_files_sim
generate_boot_files_sim: ## to generate boot files for simulation
generate_boot_files_sim: update_xlen
	@echo "XLEN=$(XLEN)" > boot/Makefile.inc
	@mkdir -p bin
	@cd boot/; make;
	@cut -c1-8 boot/boot.hex > bin/boot.MSB
	@if [ "$(XLEN)" = "64" ]; then\
	  cut -c9-16 boot/boot.hex > bin/boot.LSB;\
    else cp bin/boot.MSB bin/boot.LSB;\
  fi

.PHONY: ip_build
ip_build: ## build Xilinx Core-IPs used in this project
	vivado -log ipbuild.log -nojournal -mode tcl -notrace -source boards/$(BOARD)/tcl/create_ip_project.tcl \
		-tclargs $(FPGA) $(XLEN) $(ISA) $(JOBS) $(buswidth) $(BUS_WIDTH)\
		|| (echo "Could not create IP project"; exit 1)

.PHONY: board_build
board_build:
	vivado -nojournal -nolog -mode tcl -notrace -source boards/$(BOARD)/tcl/create_project.tcl -tclargs $(SYNTHTOP) $(FPGA) $(ISA) $(JTAG_TYPE) $(VERILOGDIR) $(BUS_WIDTH)\
	|| (echo "Could not create core project"; exit 1)
	vivado -nojournal -log artybuild.log -notrace -mode tcl -source boards/$(BOARD)/tcl/run.tcl \
		-tclargs $(JOBS) || (echo "ERROR: While running synthesis")
ifeq ($(MCS),true)
	@make generate_mcs BOARD=$(BOARD)
endif

.PHONY: program_fpga_jlink
program_fpga_jlink: ## program the fpga
	$(Vivado_loc) -nojournal -nolog -mode tcl -source boards/$(BOARD)/tcl/program.tcl

.PHONY: generate_mcs
generate_mcs: ## Generate the FPGA Configuration Memory file.
	vivado -nojournal -nolog -mode tcl -source boards/$(BOARD)/tcl/generate_mcs.tcl

.PHONY: program_mcs
program_mcs: ## Program the FPGA Configuration Memory in order to use the onboard ftdi jtag chain
	$(Vivado_loc) -nojournal -nolog -mode tcl -source boards/$(BOARD)/tcl/program_mcs.tcl
	echo "Please Disconnect and reconnect Your Arty Board from your PC"
	echo "After programming reset the device once and run \"sudo openocd \
	-f shakti-arty.cfg\" to start a gdb server at localhost:3333 "

.PHONY: program_bitstream
program_bitstream: ## Program the FPGA bitstream directly to the FPGA device
	$(Vivado_loc) -nojournal -nolog -mode tcl -source boards/$(BOARD)/tcl/program.tcl
	echo "Bitstream programmed to FPGA."
	echo "Run \"sudo openocd -f shakti-arty.cfg\" to start GDB server at localhost:3333"


.PHONY: clean
clean: ## clean bsv build director
	rm -rf build

.PHONY: clean_verif
clean_verif:
	rm -rf verification/workdir/*
	rm -rf verification/riscv-torture/output/riscv-torture
.PHONY: clean_all
clean_all: ## Clean all things including BSV and fpga_project and verification
	rm -rf build
	rm -rf verification/workdir/*
	rm -rf verification/riscv-torture/output/riscv-torture
	rm -rf fpga_project
		@python3 -m configure.main --ispec sample_config/c$(XLEN_build)_$(BOARD)/rv$(XLEN_build)i_isa.yaml \
	--customspec sample_config/c$(XLEN_build)_$(BOARD)/rv$(XLEN_build)i_custom.yaml \
	--cspec sample_config/c$(XLEN_build)_$(BOARD)/core$(XLEN_build).yaml \
	--gspec sample_config/c$(XLEN_build)_$(BOARD)/csr_grouping$(XLEN_build).yaml \
	--dspec sample_config/c$(XLEN_build)_$(BOARD)/rv$(XLEN_build)i_debug.yaml --clean

# =============================================================================
# Peripheral Automation Framework (PoC) - Optimized Sequential Flow
# =============================================================================

.PHONY: update_bsvpath run_peripheral_bsv run_peripheral_bsv_dry
.PHONY: run_peripheral_verilog run_peripheral_verilog_dry
.PHONY: run_peripheral_xdc run_peripheral_xdc_dry
.PHONY: run_peripheral_complete run_peripheral_complete_dry
.PHONY: track_pin_map
.PHONY: sync_bsv_patches sync_automated_files
.PHONY: restore_peripheral_patches pre_validate_automation post_validate_automation quick_build_automated build_automated

# === PIN MAP TRACKING (Uses BOARD/FPGA from Makefile) ===
track_pin_map:
	@echo "Tracking pin map for $(BOARD) ($(FPGA))..."
	@python3 scripts/track_pin_map.py \
		boards/$(BOARD)/master_constraints.xdc \
		boards/$(BOARD)/pin_map.yaml \
		--board $(BOARD) --fpga $(FPGA) \
		--active_xdc boards/$(BOARD)/constraints.xdc
	@echo "Pin map tracked: boards/$(BOARD)/pin_map.yaml"

# === UPDATE BSVPATH ONLY (runs before run_setup_build) ===
update_bsvpath:
	@echo "Updating bsvpath for added peripherals..."
	@python3 -c "import yaml" 2>/dev/null || pip3 install pyyaml --quiet
	@python3 scripts/peripheral_auto_integrator.py --config soc_build_config.yaml --bsvpath

# === PHASE 1: BSV/SoC Integration (Pre-Verilog) ===
run_peripheral_bsv:
	@echo "Phase 1: Applying BSV/SoC patches..."
	@python3 scripts/peripheral_auto_integrator.py --config soc_build_config.yaml --bsv

run_peripheral_bsv_dry:
	@echo "Phase 1 DRY-RUN: BSV/SoC patches..."
	@python3 scripts/peripheral_auto_integrator.py --config soc_build_config.yaml --bsv --dry-run --verbose

# === PHASE 2: Verilog Routing (Post-`generate_verilog`) ===
run_peripheral_verilog:
	@echo "Phase 2: Applying fpga_top.v patches..."
	@python3 scripts/peripheral_auto_integrator.py --config soc_build_config.yaml --verilog

run_peripheral_verilog_dry:
	@echo "Phase 2 DRY-RUN: fpga_top.v patches..."
	@python3 scripts/peripheral_auto_integrator.py --config soc_build_config.yaml --verilog --dry-run --verbose

# === PHASE 3: XDC Constraints (Post-Verilog Routing) ===
run_peripheral_xdc:
	@echo "Phase 3: Applying XDC constraints..."
	@python3 scripts/peripheral_auto_integrator.py --config soc_build_config.yaml --xdc

run_peripheral_xdc_dry:
	@echo "Phase 3 DRY-RUN: XDC constraints..."
	@python3 scripts/peripheral_auto_integrator.py --config soc_build_config.yaml --xdc --dry-run --verbose

# === COMPLETE DRY-RUN (All phases, validates anchors) ===
run_peripheral_automation_dry:
	@echo "=== COMPLETE DRY-RUN: Validating context & patches ==="
	@python3 -c "import yaml; c=yaml.safe_load(open('soc_build_config.yaml')); \
		periph=c['automated_peripherals'][0]['context']; \
		assert periph.get('verilog_port_name'), '[ERROR] verilog_port_name missing in soc_build_config.yaml'; \
		assert periph.get('rtc_pin_board_pin'), '[ERROR] rtc_pin_board_pin missing in soc_build_config.yaml'; \
		print('[INFO] Context validation passed')"
	@echo "[INFO] Running BSV phase dry-run..."
	@$(MAKE) run_peripheral_bsv_dry
	@echo "[INFO] Running Verilog phase dry-run..."
	@$(MAKE) run_peripheral_verilog_dry
	@echo "[INFO] Running XDC phase dry-run..."
	@$(MAKE) run_peripheral_xdc_dry
	@echo "[INFO] Complete dry-run finished. No files modified."

# === COMPLETE RUN (All phases, applies patches) ===
run_peripheral_automation:
	@echo "=== COMPLETE RUN: Applying all phases ==="
	@python3 -c "import yaml; c=yaml.safe_load(open('soc_build_config.yaml')); \
		periph=c['automated_peripherals'][0]['context']; \
		assert periph.get('verilog_port_name'), '[ERROR] verilog_port_name missing in soc_build_config.yaml'; \
		assert periph.get('rtc_pin_board_pin'), '[ERROR] rtc_pin_board_pin missing in soc_build_config.yaml'; \
		print('[INFO] Context validation passed')"
	@echo "[INFO] Updating bsvpath ..."
	@$(MAKE) update_bsvpath
	@echo "[INFO] Running BSV phase..."
	@$(MAKE) run_peripheral_bsv || { echo "[ERROR] BSV phase failed"; exit 1; }
	@echo "[INFO] Running Verilog phase..."
	@$(MAKE) run_peripheral_verilog || { echo "[ERROR] Verilog phase failed"; exit 1; }
	@echo "[INFO] Running XDC phase..."
	@$(MAKE) run_peripheral_xdc || { echo "[ERROR] XDC phase failed"; exit 1; }
	@echo "[INFO] Complete run finished. All phases applied."
	@echo "[INFO] Tracking pin map to sync with new constraints..."
	@$(MAKE) track_pin_map

# === UTILITIES ===
restore_peripheral_patches:
	@echo "Restoring pre-automation state..."
	@python3 scripts/peripheral_auto_integrator.py --restore
	@# Explicitly restore bsvpath if backup exists (guarantees root-file restoration)
	@if [ -f ".automation_backup/bsvpath" ]; then \
		cp -f .automation_backup/bsvpath ./bsvpath && echo " [INFO] Restored bsvpath"; \
	fi
	@echo "Resyncing pin map to match restored constraints..."
	@$(MAKE) track_pin_map
	@echo "[INFO] Restoration complete. Removing backup files..."
	@rm -rf .automation_backup

sync_bsv_patches:
	@echo "Syncing patched BSV files from boards/$(BOARD)/ to hw/ root..."
	@cp -f boards/$(BOARD)/Soc.bsv .
	@cp -f boards/$(BOARD)/Soc.defines .
	@cp -f boards/$(BOARD)/mixed_cluster.bsv .
#	@cp -f boards/$(BOARD)/bsvpath . 2>/dev/null || true
	@# Touch files to force make to see them as newer than .bo artifacts
	@touch Soc.bsv Soc.defines mixed_cluster.bsv bsvpath 2>/dev/null || true
	@echo "[INFO] BSV patches synced to hw/ root"

sync_automated_files:
	@echo "Syncing automated files from boards/$(BOARD)/ to hw/ root..."
	@echo "Retracking pin map to reflect latest constraints..."
	@$(MAKE) track_pin_map
	@cp -f boards/$(BOARD)/constraints.xdc .
	@cp -f boards/$(BOARD)/Soc.bsv .
	@cp -f boards/$(BOARD)/Soc.defines .
	@cp -f boards/$(BOARD)/mixed_cluster.bsv .
	@cp -f boards/$(BOARD)/fpga_top.v .
#	@cp -f boards/$(BOARD)/bsvpath . 2>/dev/null || true
	@echo "[INFO] Sync complete: All FPGA files copied from boards/$(BOARD)/ to hw/ root"

pre_validate_automation:
	@echo "[INFO] Pre-validating peripheral automation..."
	@./ip_bsv/rtc_v1/rtc_validate.sh --pre --verbose

post_validate_automation:
	@echo "[INFO] Post-validating peripheral automation..."
	@./ip_bsv/rtc_v1/rtc_validate.sh --post --verbose

# === OPTIMIZED FULL AUTOMATED BUILD FLOW ===
.PHONY: quick_build_automated
quick_build_automated: pre_validate_automation
	@echo "[INFO] Updating bsvpath ..."
	@$(MAKE) update_bsvpath
	@echo "[INFO] Running setup build..."
	@$(MAKE) run_setup_build
	@echo "[INFO] Tracking pin map..."
	@$(MAKE) track_pin_map
ifeq ($(SKIP_DRY_RUN),)
	@echo "[INFO] Running safety dry-run (set SKIP_DRY_RUN=1 to skip)..."
	@$(MAKE) run_peripheral_automation_dry
endif
	@echo "[INFO] Applying BSV patches..."
	@$(MAKE) run_peripheral_bsv
	@$(MAKE) sync_bsv_patches
	@echo "[INFO] Generating Verilog (BSV [INFO] Verilog compilation)..."
	@$(MAKE) generate_verilog -j
	@echo "[INFO] Applying Verilog routing patches..."
	@$(MAKE) run_peripheral_verilog
	@echo "[INFO] Applying XDC constraints..."
	@$(MAKE) run_peripheral_xdc
	@$(MAKE) sync_automated_files
	@echo "[INFO] Running post-validation..."
	@$(MAKE) post_validate_automation
	@echo "[INFO] Generating boot files, IP, and board bitstream..."
	@$(MAKE) generate_boot_files ip_build board_build
	@echo "[INFO] Automated build complete for $(BOARD)"

# === TOP-LEVEL ENTRY POINT ===
.PHONY: build_automated
build_automated: ## Build with full peripheral automation and validation
	@make quick_build_automated

# === OPTIONAL: Override default 'build' target if AUTOMATE_PERIPHERALS=1 ===
# Uncomment below to make 'make build' use automation by default
# build:
# ifeq ($(AUTOMATE_PERIPHERALS),1)
# 	@make build_automated
# else
# 	@make quick_build
# endif
