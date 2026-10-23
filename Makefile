# Develop here, test and trial on nuc01.
#
# nuc01 runs Odoo 10 in Docker (project /opt/odoo10o3):
#   odoo:     container odoo10o3_odoo_1, runs as user odoo:
#             python /opt/odoo/sources/odoo/odoo-bin -c /opt/odoo/etc/odoo.conf
#   postgres: container odoo10o3_postgres_1, port 5455
# The live server's addons_path is generated at container start from
# /opt/odoo10o3/addons/oca_dependencies.txt. The targets below don't rely on
# it: they pass --addons-path explicitly, so nothing in the server config
# changes.
#
# `deploy` copies the working tree, uncommitted changes included, so changes
# can be tested before they are committed.

NUC      = jdm@192.168.50.44
SSH      = ssh -o BatchMode=yes $(NUC)
REMOTE   = /opt/odoo10o3/addons/odoo_account_addons
ODOO     = odoo10o3_odoo_1
PG       = odoo10o3_postgres_1
PGPORT   = 5455
TESTDB   = bst_test
SOURCEDB = ogecko_2026Q3
MODULE   = account_bank_statement_template

ODOO_ADDONS = $(shell $(SSH) "docker exec $(ODOO) sed -n 's/^addons_path *= *//p' /opt/odoo/etc/odoo.conf" 2>/dev/null),/opt/odoo/additional_addons/odoo_account_addons
# Alternate ports so a run never collides with the live server.
ODOO_RUN = $(SSH) "docker exec -u odoo $(ODOO) python /opt/odoo/sources/odoo/odoo-bin \
	-c /opt/odoo/etc/odoo.conf --addons-path=$(ODOO_ADDONS) \
	--xmlrpc-port=8099 --longpolling-port=8098 --stop-after-init
PSQL_ADMIN = $(SSH) "docker exec $(PG)
# Odoo 10's -i only refreshes the module list when base itself is installed or
# updated, so a new module is silently skipped. Refresh it first (the same as
# Apps > Update Apps List).
REGISTER = echo "env['ir.module.module'].update_list(); env.cr.commit()" | \
	$(SSH) "docker exec -i -u odoo $(ODOO) python /opt/odoo/sources/odoo/odoo-bin shell \
	-c /opt/odoo/etc/odoo.conf --addons-path=$(ODOO_ADDONS) \
	--xmlrpc-port=8099 --longpolling-port=8098 --log-level=warn

RSYNC_EXCLUDES = --exclude .git --exclude data --exclude docs/superpowers \
	--exclude tmp --exclude .superpowers --exclude .venv --exclude __pycache__ \
	--exclude '*.pyc' --exclude .pytest_cache

.PHONY: pure deploy diff test upgrade restart

# Odoo-free unit tests, run locally.
pure:
	uv run pytest -q

deploy:
	rsync -az --delete $(RSYNC_EXCLUDES) ./ $(NUC):$(REMOTE)/

# What deploy would change on nuc01.
diff:
	rsync -azn --delete --itemize-changes $(RSYNC_EXCLUDES) ./ $(NUC):$(REMOTE)/

# Run the addon's Odoo tests in a throwaway copy of $(SOURCEDB), dropped
# afterwards. The copy is a dump/restore snapshot rather than createdb -T,
# which fails whenever the live server or a browser has $(SOURCEDB) open.
test: deploy
	-$(PSQL_ADMIN) dropdb -U postgres -p $(PGPORT) --if-exists $(TESTDB)"
	$(PSQL_ADMIN) createdb -U postgres -p $(PGPORT) -O odoo $(TESTDB)"
	$(PSQL_ADMIN) sh -c 'set -e; pg_dump -U postgres -p $(PGPORT) -Fc $(SOURCEDB) > /tmp/$(TESTDB).dump; \
	  pg_restore -l /tmp/$(TESTDB).dump | grep -v \"COMMENT - EXTENSION plpgsql\" > /tmp/$(TESTDB).toc; \
	  pg_restore -U postgres -p $(PGPORT) --no-owner --role=odoo -L /tmp/$(TESTDB).toc -d $(TESTDB) /tmp/$(TESTDB).dump 2>&1 | grep -v \"no privileges\" || true; \
	  rm -f /tmp/$(TESTDB).dump /tmp/$(TESTDB).toc'"
	$(REGISTER) -d $(TESTDB)" > tmp/odoo-test.log 2>&1; \
	  $(ODOO_RUN) -d $(TESTDB) -i $(MODULE) --test-enable --log-level=test" >> tmp/odoo-test.log 2>&1; \
	  rc=$$?; \
	  $(PSQL_ADMIN) dropdb -U postgres -p $(PGPORT) --if-exists $(TESTDB)"; \
	  grep -E ' (ERROR|FAIL)|: (FAIL|ERROR):|Traceback|invalid module|$(MODULE)\.tests' tmp/odoo-test.log | tail -40; \
	  if [ $$rc -ne 0 ] || grep -qE ' (ERROR|CRITICAL) |^(FAIL|ERROR):|invalid module names' tmp/odoo-test.log \
	     || ! grep -q '$(MODULE)\.tests' tmp/odoo-test.log; then \
	    echo "make test: FAILED (full log: tmp/odoo-test.log)"; exit 1; \
	  else echo "make test: PASSED"; fi

# Install or upgrade the addon in DB for manual trials. Only $(SOURCEDB) or a
# bst_* throwaway copy are allowed.
upgrade: deploy
	@case "$(DB)" in $(SOURCEDB)|bst_*) ;; *) echo "refusing to touch '$(DB)'"; exit 1;; esac
	$(REGISTER) -d $(DB)" > tmp/odoo-upgrade.log 2>&1
	$(ODOO_RUN) -d $(DB) -i $(MODULE) -u $(MODULE)" >> tmp/odoo-upgrade.log 2>&1 \
	  && ! grep -qE ' (ERROR|CRITICAL) |invalid module names' tmp/odoo-upgrade.log \
	  && echo "upgraded $(MODULE) in $(DB)" || { tail -30 tmp/odoo-upgrade.log; exit 1; }

# Restarts the LIVE Odoo server on nuc01 so it loads new code.
restart:
	$(SSH) "docker restart $(ODOO)"
