SERVICE ?= irc-to-mail
PYTHON ?= python3

ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
VENV := $(ROOT)/.venv
ENV_FILE := $(ROOT)/.env
UNIT := /etc/systemd/system/$(SERVICE).service
RUN_USER ?= $(or $(SUDO_USER),$(shell id -un))

.PHONY: all venv run install uninstall status logs clean

all: venv

$(VENV)/bin/python:
	$(PYTHON) -m venv $(VENV) || { rm -rf $(VENV); false; }

$(VENV)/.installed: requirements.txt | $(VENV)/bin/python
	$(VENV)/bin/python -m pip install -r requirements.txt
	touch $@

venv: $(VENV)/.installed

$(ENV_FILE):
	cp .env.example $@
	@echo "Fill in $@ then run make again." >&2
	@false

# Run in the foreground with the parameters from .env.
run: venv $(ENV_FILE)
	set -a && . $(ENV_FILE) && set +a && \
	$(VENV)/bin/python main.py -n "$$ITM_NICKNAME" -a "$$ITM_ADDRESS" \
		-u "$$ITM_USERNAME" -p "$$ITM_PASSWORD" \
		--ssl "$$ITM_SSL" --level "$$ITM_LEVEL"

install: venv $(ENV_FILE)
	printf '%s\n' \
		'[Unit]' \
		'Description=IRC to hooks forwarder' \
		'Wants=network-online.target' \
		'After=network-online.target' \
		'' \
		'[Service]' \
		'User=$(RUN_USER)' \
		'WorkingDirectory=$(ROOT)' \
		'EnvironmentFile=$(ENV_FILE)' \
		'ExecStart=$(VENV)/bin/python main.py -n $${ITM_NICKNAME} -a $${ITM_ADDRESS} -u $${ITM_USERNAME} -p $${ITM_PASSWORD} --ssl $${ITM_SSL} --level $${ITM_LEVEL}' \
		'Restart=always' \
		'RestartSec=5' \
		'' \
		'[Install]' \
		'WantedBy=multi-user.target' > $(UNIT)
	systemctl daemon-reload
	systemctl enable --now $(SERVICE)
	systemctl restart $(SERVICE)

uninstall:
	-systemctl disable --now $(SERVICE)
	rm -f $(UNIT)
	systemctl daemon-reload

status:
	systemctl status $(SERVICE)

logs:
	journalctl -u $(SERVICE) -f

clean:
	rm -rf $(VENV)
