# Makefile for Peripheral Auto-Integration setup for gc2025 - nexys_video board

# =============================================================================
# Shakti GC2025 Auto-Integration Manager
# =============================================================================

# --- ARGUMENTS (with defaults) ---
SETUP_PATH    ?= $(HOME)/Desktop/shakti-dev/workspace3
CLONE_NAME    ?= gc2025_autop
CONFIG_SELECT ?= soc_build_config1.yaml
BOARD         ?= nexys_video
FORCE		  ?= 0

# --- INTERNAL VARIABLES ---
CLONE_DIR     := $(SETUP_PATH)/$(CLONE_NAME)
HW_DIR        := $(CLONE_DIR)/hw
BOARD_DIR     := $(HW_DIR)/boards/$(BOARD)
REPO_LINK	  := https://gitlab.com/shaktiproject/gc2025

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
	@echo "Usage: make <target> SETUP_PATH=../path CLONE_NAME=my_clone CONFIG_SELECT=cfg.yaml BOARD=nexys_video"
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
	@if [ ! -f "configs/$(CONFIG_SELECT)" ]; then \
		echo "$(RED)[ERROR] Config file 'configs/$(CONFIG_SELECT)' not found!$(NC)"; \
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
	
	@echo "$(BLUE)[5/7] Deploying selected configuration: $(CONFIG_SELECT)...$(NC)"
	@cp configs/$(CONFIG_SELECT) $(HW_DIR)/soc_build_config.yaml
	
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
	@rm -f $(HW_DIR)/README_AUTOMATION.md $(HW_DIR)/UserManual.md
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
# =============================================================================
# FRAMEWORK CANDIDATE DISCOVERY (used by `status`)
# =============================================================================
# Every archived legacy framework is a candidate, discovered by glob -- adding a
# new archive/<name>/ needs no edit here. `status` scores the clone against the
# V2 payload and against each of these, then reports and diffs against whichever
# one it most resembles.
LEGACY_FILE_DIRS := $(sort $(wildcard archive/*/files))

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
		score_of() { \
			base="$$1"; s=0; \
			for item in scripts ip_bsv templates Makefile master_constraints.xdc; do \
				hw_target="$(HW_DIR)/$$item"; \
				if [ "$$item" = "master_constraints.xdc" ]; then hw_target="$(HW_DIR)/boards/$(BOARD)/master_constraints.xdc"; fi; \
				if [ -d "$$base/$$item" ]; then \
					for f in $$(find "$$base/$$item" -type f 2>/dev/null | grep -v '__pycache__' | grep -v '\.pyc$$'); do \
						rel=$${f#$$base/}; \
						if [ -f "$(HW_DIR)/$$rel" ] && diff -wBq "$$f" "$(HW_DIR)/$$rel" >/dev/null 2>&1; then s=$$((s+1)); fi; \
					done; \
				elif [ -f "$$base/$$item" ]; then \
					if [ -f "$$hw_target" ] && diff -wBq "$$base/$$item" "$$hw_target" >/dev/null 2>&1; then s=$$((s+1)); fi; \
				fi; \
			done; \
			echo $$s; \
		}; \
		cfg_matches() { \
			__cdir="$$1"; \
			[ -f "$(HW_DIR)/soc_build_config.yaml" ] || return 1; \
			for __c in "$$__cdir"/*.yaml; do \
				[ -f "$$__c" ] || continue; \
				if diff -wBq "$$__c" "$(HW_DIR)/soc_build_config.yaml" >/dev/null 2>&1; then return 0; fi; \
			done; \
			return 1; \
		}; \
		tbl=$$(mktemp); \
		printf '%s\t%s\t%s\t%s\n' "MAIN V2" "files" "configs" "$$(score_of files)" >> $$tbl; \
		for lf in $(LEGACY_FILE_DIRS); do \
			ldir=$$(dirname "$$lf"); lname=$$(basename "$$ldir"); \
			printf '%s\t%s\t%s\t%s\n' "$$lname" "$$lf" "$$ldir/configs" "$$(score_of $$lf)" >> $$tbl; \
		done; \
		while IFS="$$(printf '\t')" read -r n b c s; do \
			echo "$(BLUE)  [CANDIDATE] $$n -> $$s matching file(s)$(NC)"; \
		done < $$tbl; \
		best_score=$$(cut -f4 $$tbl | sort -rn | head -1); \
		tiedf=$$(mktemp); \
		awk -F'\t' -v m="$$best_score" '$$4==m' $$tbl > $$tiedf; \
		n_tied=$$(grep -c . $$tiedf); \
		chosen=$$(head -1 $$tiedf); \
		if [ "$$n_tied" -gt 1 ]; then \
			echo "$(YELLOW)[TIE-BREAK] $$n_tied frameworks tied at $$best_score; disambiguating on soc_build_config.yaml.$(NC)"; \
			while IFS="$$(printf '\t')" read -r n b c s; do \
				if cfg_matches "$$c"; then \
					chosen=$$(printf '%s\t%s\t%s\t%s' "$$n" "$$b" "$$c" "$$s"); \
					echo "$(YELLOW)[TIE-BREAK] Active config matches a config shipped by '$$n' -- selecting it.$(NC)"; \
					break; \
				fi; \
			done < $$tiedf; \
		fi; \
		rm -f $$tiedf; \
		active_name=$$(printf '%s' "$$chosen" | cut -f1); \
		active_base=$$(printf '%s' "$$chosen" | cut -f2); \
		config_base=$$(printf '%s' "$$chosen" | cut -f3); \
		rm -f $$tbl; \
		if [ "$$active_name" = "MAIN V2" ]; then \
			active_label="MAIN V2"; \
			echo "$(GREEN)[RESEMBLANCE] Clone most resembles $$active_label (score $$best_score).$(NC)"; \
		else \
			active_label="LEGACY V1 / $$active_name"; \
			echo "$(YELLOW)[RESEMBLANCE] Clone most resembles $$active_label (score $$best_score) -- DEPRECATED architecture.$(NC)"; \
		fi; \
		echo "$(BLUE)--- Configuration Check ($$active_label) ---$(NC)"; \
		if [ -f "$(HW_DIR)/soc_build_config.yaml" ]; then \
			if [ -f "$$config_base/$(CONFIG_SELECT)" ] && diff -wBq "$$config_base/$(CONFIG_SELECT)" "$(HW_DIR)/soc_build_config.yaml" >/dev/null 2>&1; then \
				echo "$(GREEN)[CONFIG] Active config MATCHES '$$config_base/$(CONFIG_SELECT)'.$(NC)"; \
			else \
				match_found=0; \
				for cfg in $$config_base/*.yaml; do \
					if [ -f "$$cfg" ] && diff -wBq "$$cfg" "$(HW_DIR)/soc_build_config.yaml" >/dev/null 2>&1; then \
						echo "$(YELLOW)[CONFIG] Active config differs from '$(CONFIG_SELECT)', but MATCHES '$$cfg'.$(NC)"; \
						match_found=1; break; \
					fi; \
				done; \
				if [ $$match_found -eq 0 ]; then \
					echo "$(RED)[CONFIG] Active config matches no config in $$config_base (custom/modified).$(NC)"; \
				fi; \
			fi; \
		else \
			echo "$(RED)[CONFIG] No soc_build_config.yaml found in clone!$(NC)"; \
		fi; \
		echo "$(BLUE)--- Deep Integrity Scan ($$active_label) ---$(NC)"; \
		mismatch=0; \
		for item in scripts ip_bsv Makefile master_constraints.xdc; do \
			hw_target="$(HW_DIR)/$$item"; \
			if [ "$$item" = "master_constraints.xdc" ]; then hw_target="$(HW_DIR)/boards/$(BOARD)/master_constraints.xdc"; fi; \
			if [ -d "$$active_base/$$item" ]; then \
				for f in $$(find "$$active_base/$$item" -type f 2>/dev/null | grep -v '__pycache__' | grep -v '\.pyc$$'); do \
					rel=$${f#$$active_base/}; \
					if [ ! -f "$(HW_DIR)/$$rel" ]; then \
						echo "$(RED)[MISSING] $$rel (expected by $$active_label)$(NC)"; mismatch=1; \
					elif ! diff -wBq "$$f" "$(HW_DIR)/$$rel" >/dev/null 2>&1; then \
						echo "$(YELLOW)[MODIFIED] $$rel differs from reference$(NC)"; mismatch=1; \
					fi; \
				done; \
			elif [ -f "$$active_base/$$item" ]; then \
				if [ ! -f "$$hw_target" ]; then \
					echo "$(RED)[MISSING] $$item (expected by $$active_label at $$hw_target)$(NC)"; mismatch=1; \
				elif ! diff -wBq "$$active_base/$$item" "$$hw_target" >/dev/null 2>&1; then \
					echo "$(YELLOW)[MODIFIED] $$item differs from reference$(NC)"; mismatch=1; \
				fi; \
			fi; \
			if [ -d "$(HW_DIR)/$$item" ]; then \
				for f in $$(find "$(HW_DIR)/$$item" -type f 2>/dev/null | grep -v '__pycache__' | grep -v '\.pyc$$'); do \
					rel=$${f#$(HW_DIR)/}; \
					if [ ! -f "$$active_base/$$rel" ]; then \
						echo "$(YELLOW)[ORPHAN] $$rel (present in clone, missing in $$active_label)$(NC)"; mismatch=1; \
					fi; \
				done; \
			elif [ -f "$$hw_target" ]; then \
				if [ ! -f "$$active_base/$$item" ]; then \
					echo "$(YELLOW)[ORPHAN] $$item (present in clone, missing in $$active_label)$(NC)"; mismatch=1; \
				fi; \
			fi; \
		done; \
		if [ $$mismatch -eq 0 ]; then \
			echo "$(GREEN)[INTEGRITY] All scripts and IP files MATCH $$active_label perfectly.$(NC)"; \
		fi; \
		echo "$(BLUE)--- Documentation Check ($$active_label) ---$(NC)"; \
		doc_mismatch=0; \
		if [ "$$active_name" = "MAIN V2" ]; then doc_base="docs"; else doc_base="archive/$$active_name/docs"; fi; \
		if [ -d "$$doc_base" ]; then \
			for f in $$(find "$$doc_base" -type f 2>/dev/null | grep -v '__pycache__' | grep -v '\.pyc$$'); do \
				rel=$${f#$$doc_base/}; \
				if [ ! -f "$(HW_DIR)/$$rel" ]; then \
					echo "$(RED)[DOC NOTE] Missing in clone: $$rel$(NC)"; doc_mismatch=1; \
				elif ! diff -wBq "$$f" "$(HW_DIR)/$$rel" >/dev/null 2>&1; then \
					echo "$(YELLOW)[DOC NOTE] Modified in clone: $$rel$(NC)"; doc_mismatch=1; \
				fi; \
			done; \
		else \
			echo "$(YELLOW)[DOC NOTE] No docs/ directory for $$active_label$(NC)"; \
		fi; \
		if [ $$doc_mismatch -eq 0 ]; then \
			echo "$(GREEN)[DOCS] Documentation MATCHES $$active_label perfectly.$(NC)"; \
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
	@cp configs/$(CONFIG_SELECT) $(HW_DIR)/soc_build_config.yaml
	@echo "$(GREEN)[SUCCESS] Hot-reloaded framework files, docs, and config into clone.$(NC)"
