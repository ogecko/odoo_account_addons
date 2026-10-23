# Develop here; test against the local Odoo stack in ~/projects/ogecko_accounts.
#
# The live stack's Odoo container (ogecko-odoo-1) has this working tree mounted
# read-only at /opt/odoo/dev_addons/odoo_account_addons. Tests run a separate,
# temporary odoo-bin process in that container, with this tree in place of the
# committed clone on the addons path, against a throwaway copy of $(SOURCEDB)
# that is dropped afterwards. The live server never loads this tree, and
# nothing here writes to a live database.
#
# The live server runs committed code only: commit here, then in
# ~/projects/ogecko_accounts run `make addon-pull` and `make addon-upgrade DB=...`.

ODOO     = ogecko-odoo-1
PG       = ogecko-postgres-1
PGPORT   = 5455
TESTDB   = bst_test
SOURCEDB = ogecko_2026Q3
MODULE   = account_bank_statement_template
LIVE_PATH = /opt/odoo/additional_addons/odoo_account_addons
DEV_PATH  = /opt/odoo/dev_addons/odoo_account_addons

# The live addons path with this working tree in place of the committed clone.
ODOO_ADDONS = $(shell docker exec $(ODOO) sed -n 's/^addons_path *= *//p' /opt/odoo/etc/odoo.conf 2>/dev/null | sed 's|$(LIVE_PATH)|$(DEV_PATH)|')
# No servers, no scheduled jobs, so a run never collides with the live server.
ODOO_RUN = docker exec -u odoo $(ODOO) python /opt/odoo/sources/odoo/odoo-bin \
	-c /opt/odoo/etc/odoo.conf --addons-path=$(ODOO_ADDONS) \
	--no-xmlrpc --max-cron-threads=0 --stop-after-init
PG_EXEC = docker exec $(PG)
# Odoo 10's -i only refreshes the module list when base itself is installed or
# updated, so a new module is silently skipped. Refresh it first (the same as
# Apps > Update Apps List).
REGISTER = echo "env['ir.module.module'].update_list(); env.cr.commit()" | \
	docker exec -i -u odoo $(ODOO) python /opt/odoo/sources/odoo/odoo-bin shell \
	-c /opt/odoo/etc/odoo.conf --addons-path=$(ODOO_ADDONS) \
	--no-xmlrpc --max-cron-threads=0 --log-level=warn

.PHONY: pure test upgrade check-stack deploy diff

# Odoo-free unit tests, run locally.
pure:
	uv run pytest -q

check-stack:
	@docker exec $(ODOO) test -d $(DEV_PATH)/$(MODULE) || { \
	  echo "the live stack isn't running with this tree mounted: cd ~/projects/ogecko_accounts && make up"; exit 1; }
	@case "$(ODOO_ADDONS)" in *$(DEV_PATH)*) ;; *) echo "could not build the test addons path"; exit 1;; esac

# Not needed any more: the working tree is mounted read-only into the stack.
deploy diff:
	@echo "nothing to do: the dev tree is mounted read-only into the local stack"

# Run the addon's Odoo tests in a throwaway copy of $(SOURCEDB), dropped
# afterwards (dump/restore, which works while the live server has it open).
test: check-stack
	@mkdir -p tmp
	-@$(PG_EXEC) dropdb -U postgres -p $(PGPORT) --if-exists $(TESTDB)
	$(PG_EXEC) createdb -U postgres -p $(PGPORT) -O odoo $(TESTDB)
	$(PG_EXEC) sh -c 'set -e; pg_dump -U postgres -p $(PGPORT) -Fc $(SOURCEDB) > /tmp/$(TESTDB).dump; \
	  pg_restore -l /tmp/$(TESTDB).dump | grep -v "COMMENT - EXTENSION plpgsql" > /tmp/$(TESTDB).toc; \
	  pg_restore -U postgres -p $(PGPORT) --no-owner --role=odoo -L /tmp/$(TESTDB).toc -d $(TESTDB) /tmp/$(TESTDB).dump 2>&1 | grep -v "no privileges" || true; \
	  rm -f /tmp/$(TESTDB).dump /tmp/$(TESTDB).toc'
	$(REGISTER) -d $(TESTDB) > tmp/odoo-test.log 2>&1; \
	  $(ODOO_RUN) -d $(TESTDB) -i $(MODULE) --test-enable --log-level=test >> tmp/odoo-test.log 2>&1; \
	  rc=$$?; \
	  $(PG_EXEC) dropdb -U postgres -p $(PGPORT) --if-exists $(TESTDB); \
	  docker exec $(ODOO) rm -rf /opt/odoo/data/filestore/$(TESTDB); \
	  grep -E ' (ERROR|FAIL)|: (FAIL|ERROR):|Traceback|invalid module|$(MODULE)\.tests.*Ran ' tmp/odoo-test.log | tail -40; \
	  if [ $$rc -ne 0 ] || grep -qE ' (ERROR|CRITICAL) |^(FAIL|ERROR):|invalid module names' tmp/odoo-test.log \
	     || ! grep -q '$(MODULE)\.tests' tmp/odoo-test.log; then \
	    echo "make test: FAILED (full log: tmp/odoo-test.log)"; exit 1; \
	  else echo "make test: PASSED"; fi

# Install or upgrade this working tree's addon in a throwaway bst_* database.
# Live databases get committed code only (see the top of this file).
upgrade: check-stack
	@case "$(DB)" in bst_*) ;; *) echo "refusing to touch '$(DB)': only bst_* copies (live databases use make addon-upgrade in ogecko_accounts)"; exit 1;; esac
	@mkdir -p tmp
	$(REGISTER) -d $(DB) > tmp/odoo-upgrade.log 2>&1
	$(ODOO_RUN) -d $(DB) -i $(MODULE) -u $(MODULE) >> tmp/odoo-upgrade.log 2>&1 \
	  && ! grep -qE ' (ERROR|CRITICAL) |invalid module names' tmp/odoo-upgrade.log \
	  && echo "upgraded $(MODULE) in $(DB)" || { tail -30 tmp/odoo-upgrade.log; exit 1; }
