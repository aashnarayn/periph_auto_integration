# Makefile for Peripheral Auto-Integration Legacy setup for gc2025 - nexys_video board

# =============================================================================
# Shakti GC2025 Auto-Integration Manager - Legacy V1 framework (RTC)
# =============================================================================

# --- ARGUMENTS (with defaults) ---
SETUP_PATH    ?= $(HOME)/Desktop/shakti-dev/workspace3
CLONE_NAME    ?= gc2025_autortc
BOARD         ?= nexys_video
FORCE		  ?= 0

# --- INTERNAL VARIABLES ---
CLONE_DIR     := $(SETUP_PATH)/$(CLONE_NAME)
HW_DIR        := $(CLONE_DIR)/hw
BOARD_DIR     := $(HW_DIR)/boards/$(BOARD)
REPO_LINK	  := https://gitlab.com/shaktiproject/gc2025
CONFIG_FILE   := soc_build_config.yaml

# ANSI Colors for CLI output
GREEN  := \033[0;32m
YELLOW := \033[1;33m
BLUE   := \033[0;34m
RED    := \033[0;31m
NC     := \033[0m

# =============================================================================
# HELP TARGET (Default)
# =============================================================================
.PHONY: help
help:
	@echo "$(BLUE)======= Shakti GC2025 Auto-Integration Manager =======$(NC)"
	@echo "Usage: make <target> SETUP_PATH=../path CLONE_NAME=my_clone BOARD=nexys_video"
	@echo ""
	@echo "$(YELLOW)Available Targets:$(NC)"
	@echo "  setup_full        : (A) Clones repo, injects framework, tracks pin map, and runs run_setup_build."
	@echo "  setup_base        : (B) Same as setup_full, but skips run_setup_build."
	@echo "  remove_automation : (C) Removes framework files from clone and restores original Makefile."
	@echo "  delete_clone      : (D) Warns about status, then completely deletes the cloned repo."
	@echo "  status            : (E) Checks if clone exists, if framework is injected, and if config matches."
	@echo "  update_framework  : (F) Pushes latest script/Makefile changes into existing clone without re-cloning."
	@echo ""

# =============================================================================
# VALIDATION CHECKS
# =============================================================================
.PHONY: _check_config
_check_config:
	@if [ ! -f "configs/$(CONFIG_FILE)" ]; then \
		echo "$(RED)[ERROR] Config file 'configs/$(CONFIG_FILE)' not found!$(NC)"; \
		exit 1; \
	fi

.PHONY: _check_clone_exists
_check_clone_exists:
	@if [ -d "$(CLONE_DIR)" ]; then \
		if [ "$(FORCE)" != "1" ]; then \
			echo "$(RED)[ERROR] Target directory $(CLONE_DIR) already exists!$(NC)"; \
			echo "Use 'make delete_clone' first, specify a different CLONE_NAME, or pass FORCE=1."; \
			exit 1; \
		else \
			echo "$(YELLOW)[WARNING] Target directory $(CLONE_DIR) already exists!$(NC)"; \
			echo "Continuing on existing clone as FORCE=1 has been set."; \
		fi; \
	fi

# =============================================================================
# TARGET A & B: SETUP ROUTINES
# =============================================================================

# Internal core setup (Steps A.1 to A.7)
.PHONY: _setup_core
_setup_core: _check_config _check_clone_exists
	@if [ ! -d "$(CLONE_DIR)" ]; then \
		echo "$(BLUE)[1/7] Cloning gc2025 into $(CLONE_DIR)...$(NC)"; \
		git clone $(REPO_LINK) $(CLONE_DIR); \
	else \
		echo "$(BLUE)[1/7] Clone already exists (FORCE=1). Skipping git clone...$(NC)"; \
	fi
	
	@echo "$(BLUE)[2/7] Backing up original Makefile and injecting framework Makefile...$(NC)"
	@cp $(HW_DIR)/Makefile $(HW_DIR)/Makefile.orig
	@cp files/Makefile $(HW_DIR)/Makefile
	
	@echo "$(BLUE)[3/7] Copying scripts/ and ip_bsv/ to hw/ root...$(NC)"
	@cp -r files/scripts $(HW_DIR)/
	@cp -r files/ip_bsv $(HW_DIR)/
	
	@echo "$(BLUE)[4/7] Copying documentation to hw/ root...$(NC)"
	@cp -r docs/* $(HW_DIR)/
	
	@echo "$(BLUE)[5/7] Deploying configuration: $(CONFIG_FILE)...$(NC)"
	@cp configs/$(CONFIG_FILE) $(HW_DIR)/soc_build_config.yaml
	
	@echo "$(BLUE)[6/7] Deploying master constraints for board: $(BOARD)...$(NC)"
	@mkdir -p $(BOARD_DIR)
	@cp files/master_constraints.xdc $(BOARD_DIR)/master_constraints.xdc
	
	@echo "$(BLUE)[7/7] Initializing Pin Map Tracking...$(NC)"
	@$(MAKE) -C $(HW_DIR) track_pin_map
	
	@echo "$(GREEN)[SUCCESS] Base framework setup completed.$(NC)"

# TARGET B: Skips run_setup_build
.PHONY: setup_base
setup_base: _setup_core
	@echo "$(YELLOW)[INFO] run_setup_build skipped as requested by setup_base.$(NC)"

# TARGET A: Full setup including run_setup_build
.PHONY: setup_full
setup_full: _setup_core
	@echo "$(BLUE)[8/8] Running build setup in cloned repo...$(NC)"
	@$(MAKE) -C $(HW_DIR) run_setup_build
	@echo "$(GREEN)[SUCCESS] Full setup and build initialization completed!$(NC)"

# =============================================================================
# TARGET C: REMOVE AUTOMATION (Restore to stock)
# =============================================================================
.PHONY: remove_automation
remove_automation:
	@if [ ! -d "$(CLONE_DIR)" ]; then \
		echo "$(RED)[ERROR] Clone not found at $(CLONE_DIR).$(NC)"; exit 1; \
	fi
	@echo "$(YELLOW)[INFO] Scrubbing automation files from $(CLONE_DIR)...$(NC)"
	@rm -rf $(HW_DIR)/scripts
	@rm -rf $(HW_DIR)/ip_bsv
	@rm -f $(HW_DIR)/README_AUTOMATION.md
	@rm -f $(HW_DIR)/soc_build_config.yaml
	@rm -f $(HW_DIR)/pin_map.yaml
	@rm -f $(BOARD_DIR)/pin_map.yaml
	@rm -f $(HW_DIR)/master_constraints.xdc
	@rm -f $(BOARD_DIR)/master_constraints.xdc
	@if [ -d "$(HW_DIR)/.automation_backup" ]; then rm -rf $(HW_DIR)/.automation_backup; fi
	@if [ -f "$(HW_DIR)/Makefile.orig" ]; then \
		mv $(HW_DIR)/Makefile.orig $(HW_DIR)/Makefile; \
		echo "$(GREEN)[SUCCESS] Original Shakti Makefile restored.$(NC)"; \
	else \
		echo "$(RED)[WARN] Makefile.orig not found! Makefile remains modified.$(NC)"; \
	fi
	@echo "$(GREEN)[SUCCESS] Automation framework removed. Clone is back to stock.$(NC)"

# =============================================================================
# TARGET E: STATUS CHECK (Deep Integrity Scan)
# =============================================================================
.PHONY: status
status:
	@echo "$(BLUE)=== Status for: $(CLONE_DIR) ===$(NC)"
	@if [ ! -d "$(CLONE_DIR)" ]; then \
		echo "$(RED)[STATUS] Clone DOES NOT EXIST.$(NC)"; \
	elif [ ! -d "$(HW_DIR)/scripts" ]; then \
		echo "$(YELLOW)[STATUS] Clone exists, but framework is NOT INSTALLED (stock).$(NC)"; \
	else \
		echo "$(GREEN)[STATUS] Clone exists and framework IS INSTALLED.$(NC)"; \
		echo "$(BLUE)--- Framework Resemblance Analysis ---$(NC)"; \
		legacy_score=0; main_score=0; \
		for item in scripts ip_bsv Makefile master_constraints.xdc; do \
			hw_target="$(HW_DIR)/$$item"; \
			if [ "$$item" = "master_constraints.xdc" ]; then hw_target="$(BOARD_DIR)/master_constraints.xdc"; fi; \
			if [ -d "files/$$item" ]; then \
				for f in $$(find "files/$$item" -type f 2>/dev/null | grep -v '__pycache__' | grep -v '\.pyc$$'); do \
					rel=$${f#files/}; \
					if [ -f "$(HW_DIR)/$$rel" ] && diff -wBq "$$f" "$(HW_DIR)/$$rel" > /dev/null 2>&1; then legacy_score=$$((legacy_score+1)); fi; \
				done; \
			elif [ -f "files/$$item" ]; then \
				if [ -f "$$hw_target" ] && diff -wBq "files/$$item" "$$hw_target" > /dev/null 2>&1; then legacy_score=$$((legacy_score+1)); fi; \
			fi; \
		done; \
		if [ -d "../../files" ]; then \
			for item in scripts ip_bsv templates Makefile master_constraints.xdc; do \
				hw_target="$(HW_DIR)/$$item"; \
				if [ "$$item" = "master_constraints.xdc" ]; then hw_target="$(BOARD_DIR)/master_constraints.xdc"; fi; \
				if [ -d "../../files/$$item" ]; then \
					for f in $$(find "../../files/$$item" -type f 2>/dev/null | grep -v '__pycache__' | grep -v '\.pyc$$'); do \
						rel=$${f#../../files/}; \
						if [ -f "$(HW_DIR)/$$rel" ] && diff -wBq "$$f" "$(HW_DIR)/$$rel" > /dev/null 2>&1; then main_score=$$((main_score+1)); fi; \
					done; \
				elif [ -f "../../files/$$item" ]; then \
					if [ -f "$$hw_target" ] && diff -wBq "../../files/$$item" "$$hw_target" > /dev/null 2>&1; then main_score=$$((main_score+1)); fi; \
				fi; \
			done; \
		fi; \
		active_base="files"; active_name="LEGACY V1"; config_base="configs"; \
		if [ $$main_score -gt $$legacy_score ]; then \
			active_base="../../files"; active_name="MAIN V2"; config_base="../../configs"; \
			echo "$(YELLOW)[RESEMBLANCE] Clone heavily resembles $$active_name framework (Score: $$main_score vs $$legacy_score).$(NC)"; \
		else \
			echo "$(GREEN)[RESEMBLANCE] Clone heavily resembles $$active_name framework (Score: $$legacy_score vs $$main_score).$(NC)"; \
		fi; \
		echo "$(BLUE)--- Configuration Check ($$active_name) ---$(NC)"; \
		if [ -f "$(HW_DIR)/soc_build_config.yaml" ]; then \
			if [ -f "$$config_base/$(CONFIG_FILE)" ] && diff -wBq "$$config_base/$(CONFIG_FILE)" "$(HW_DIR)/soc_build_config.yaml" > /dev/null 2>&1; then \
				if [ "$$active_name" = "MAIN V2" ]; then \
					echo "$(YELLOW)[CONFIG] Active config MATCHES '$$config_base/$(CONFIG_FILE)'.$(NC)"; \
				else \
					echo "$(GREEN)[CONFIG] Active config MATCHES '$$config_base/$(CONFIG_FILE)'.$(NC)"; \
				fi; \
			else \
				match_found=0; \
				for cfg in $$config_base/*.yaml; do \
					if [ -f "$$cfg" ] && diff -wBq "$$cfg" "$(HW_DIR)/soc_build_config.yaml" > /dev/null 2>&1; then \
						if [ "$$active_name" = "MAIN V2" ]; then \
							echo "$(YELLOW)[CONFIG] Active config differs from '$(CONFIG_FILE)', but MATCHES '$$cfg'.$(NC)"; \
						else \
							echo "$(GREEN)[CONFIG] Active config differs from '$(CONFIG_FILE)', but MATCHES '$$cfg'.$(NC)"; \
						fi; \
						match_found=1; \
						break; \
					fi; \
				done; \
				if [ $$match_found -eq 0 ]; then \
					echo "$(RED)[CONFIG] Active config DOES NOT MATCH any stored config in $$config_base! (Custom/Modified)$(NC)"; \
				fi; \
			fi; \
		else \
			echo "$(RED)[CONFIG] No soc_build_config.yaml found in clone!$(NC)"; \
		fi; \
		echo "$(BLUE)--- Deep Integrity Scan ($$active_name) ---$(NC)"; \
		mismatch=0; \
		for item in scripts ip_bsv Makefile master_constraints.xdc; do \
			hw_target="$(HW_DIR)/$$item"; \
			if [ "$$item" = "master_constraints.xdc" ]; then hw_target="$(BOARD_DIR)/master_constraints.xdc"; fi; \
			if [ -d "$$active_base/$$item" ]; then \
				for f in $$(find "$$active_base/$$item" -type f 2>/dev/null | grep -v '__pycache__' | grep -v '\.pyc$$'); do \
					rel=$${f#$$active_base/}; \
					if [ ! -f "$(HW_DIR)/$$rel" ]; then \
						echo "$(RED)[MISSING] $$rel (expected by $$active_name)$(NC)"; \
						mismatch=1; \
					elif ! diff -wBq "$$f" "$(HW_DIR)/$$rel" > /dev/null 2>&1; then \
						echo "$(YELLOW)[MODIFIED] $$rel differs from reference$(NC)"; \
						mismatch=1; \
					fi; \
				done; \
			elif [ -f "$$active_base/$$item" ]; then \
				if [ ! -f "$$hw_target" ]; then \
					echo "$(RED)[MISSING] $$item (expected by $$active_name at $$hw_target)$(NC)"; \
					mismatch=1; \
				elif ! diff -wBq "$$active_base/$$item" "$$hw_target" > /dev/null 2>&1; then \
					echo "$(YELLOW)[MODIFIED] $$item differs from reference$(NC)"; \
					mismatch=1; \
				fi; \
			fi; \
			if [ -d "$(HW_DIR)/$$item" ]; then \
				for f in $$(find "$(HW_DIR)/$$item" -type f 2>/dev/null | grep -v '__pycache__' | grep -v '\.pyc$$'); do \
					rel=$${f#$(HW_DIR)/}; \
					if [ ! -f "$$active_base/$$rel" ]; then \
						echo "$(YELLOW)[ORPHAN] $$rel (present in clone, missing in $$active_name framework)$(NC)"; \
						mismatch=1; \
					fi; \
				done; \
			elif [ -f "$$hw_target" ]; then \
				if [ ! -f "$$active_base/$$item" ]; then \
					echo "$(YELLOW)[ORPHAN] $$item (present in clone, missing in $$active_name framework)$(NC)"; \
					mismatch=1; \
				fi; \
			fi; \
		done; \
		if [ $$mismatch -eq 0 ]; then \
			if [ "$$active_name" = "MAIN V2" ]; then \
				echo "$(YELLOW)[INTEGRITY] All scripts and IP files MATCH $$active_name perfectly.$(NC)"; \
			else \
				echo "$(GREEN)[INTEGRITY] All scripts and IP files MATCH $$active_name perfectly.$(NC)"; \
			fi; \
		fi; \
		echo "$(BLUE)--- Documentation Check ---$(NC)"; \
		doc_mismatch=0; doc_base="docs"; \
		if [ "$$active_name" = "MAIN V2" ] && [ -d "../../docs" ]; then doc_base="../../docs"; fi; \
		if [ -d "$$doc_base" ]; then \
			for f in $$(find "$$doc_base" -type f 2>/dev/null | grep -v '__pycache__' | grep -v '\.pyc$$'); do \
				rel=$${f#$$doc_base/}; \
				if [ ! -f "$(HW_DIR)/$$rel" ]; then \
					echo "$(RED)[DOC NOTE] Missing in clone: $$rel$(NC)"; \
					doc_mismatch=1; \
				elif ! diff -wBq "$$f" "$(HW_DIR)/$$rel" > /dev/null 2>&1; then \
					echo "$(YELLOW)[DOC NOTE] Modified in clone: $$rel$(NC)"; \
					doc_mismatch=1; \
				fi; \
			done; \
		fi; \
		if [ $$doc_mismatch -eq 0 ]; then \
			if [ "$$active_name" = "MAIN V2" ]; then \
				echo "$(YELLOW)[DOCS] Documentation MATCHES $$active_name perfectly.$(NC)"; \
			else \
				echo "$(GREEN)[DOCS] Documentation MATCHES perfectly.$(NC)"; \
			fi; \
		fi; \
		echo "$(BLUE)--- Additional Info ---$(NC)"; \
		if [ -d "$(HW_DIR)/scripts/__pycache__" ]; then \
			cache_files=$$(ls -1 "$(HW_DIR)/scripts/__pycache__" 2>/dev/null || true); \
			if [ -n "$$cache_files" ]; then \
				echo "$(YELLOW)[CACHE] Found __pycache__ in clone:$(NC)"; \
				for cf in $$cache_files; do echo "$(YELLOW)  - $$cf$(NC)"; done; \
			fi; \
		fi; \
		echo "$(BLUE)=== End of Status Check ===$(NC)"; \
	fi

# =============================================================================
# TARGET D: NUKE CLONE
# =============================================================================
.PHONY: delete_clone
delete_clone: status
	@echo ""
	@if [ -d "$(CLONE_DIR)" ]; then \
		read -p "⚠️  Are you sure you want to permanently delete $(CLONE_DIR)? [y/N] " ans; \
		if [ "$$ans" = "y" ] || [ "$$ans" = "Y" ]; then \
			rm -rf $(CLONE_DIR); \
			echo "$(GREEN)[SUCCESS] Clone deleted.$(NC)"; \
		else \
			echo "$(YELLOW)Deletion aborted.$(NC)"; \
		fi \
	else \
		echo "$(YELLOW)Nothing to delete.$(NC)"; \
	fi

# =============================================================================
# TARGET F: UPDATE FRAMEWORK (Developer Quality-of-Life)
# =============================================================================
.PHONY: update_framework
update_framework:
	@echo "$(BLUE)[INFO] Pushing latest framework changes to $(CLONE_DIR)...$(NC)"
	@if [ ! -d "$(HW_DIR)/scripts" ]; then \
		echo "$(RED)[ERROR] Framework not installed in clone. Run setup_base first.$(NC)"; exit 1; \
	fi
	@cp files/Makefile $(HW_DIR)/Makefile
	@rm -rf $(HW_DIR)/scripts/*
	@rm -rf $(HW_DIR)/ip_bsv/*
	@echo "$(BLUE)[INFO] Removing old documentation from clone...$(NC)"
	@if [ -d "docs" ]; then \
		for f in $$(cd docs && find . -type f); do \
			rm -f "$(HW_DIR)/$$f"; \
		done; \
	fi
	@if [ -d "$(HW_DIR)/.automation_backup" ]; then rm -rf $(HW_DIR)/.automation_backup; fi
	@echo "$(BLUE)[INFO] Injecting new files...$(NC)"
	@cp -r files/scripts/* $(HW_DIR)/scripts/.
	@cp -r files/ip_bsv/* $(HW_DIR)/ip_bsv/.
	@if [ -d "docs" ]; then cp -r docs/* $(HW_DIR)/.; fi
	@cp configs/$(CONFIG_FILE) $(HW_DIR)/soc_build_config.yaml
	@echo "$(GREEN)[SUCCESS] Hot-reloaded framework files, docs, and config into clone.$(NC)"
