# Makefile for LMNT Marketplace Plugin

.PHONY: install uninstall update

install:
	@echo "Installing LMNT Marketplace Plugin..."
	@./scripts/install.sh

uninstall:
	@echo "Uninstalling LMNT Marketplace Plugin..."
	@./scripts/uninstall.sh

update:
	@echo "Updating LMNT Marketplace Plugin..."
	@./scripts/update.sh
