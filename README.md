# odoo_account_addons
Odoo 10 addons to help with accounting, bank reconciliation and BAS preparation.

## Addons

| Addon | Purpose |
|---|---|
| `account_bank_statement_template` | Templates recognise recurring bank statement lines and propose the sales orders, purchase orders or account entries they stand for. Proposals are reviewed and approved on the statement before anything is created. |

## Layout

- One folder per addon at the top level (Odoo 10, Python 2.7).
- `account_bank_statement_template/lib/` holds Odoo-free logic, unit tested locally in `tests_pure/`.
- Each addon's `tests/` holds Odoo `TransactionCase` tests.

## Make targets

Tests run against the local Odoo stack (`ogecko_accounts`), which mounts this
working tree read-only; the live server only runs committed code.

| Target | What it does |
|---|---|
| `make pure` | Run the Odoo-free unit tests locally (`uv run pytest`). |
| `make test` | Run the addon's Odoo tests on this working tree, in a throwaway database copy that is dropped afterwards. |
| `make upgrade DB=bst_<name>` | Install or upgrade this working tree's addon in a throwaway `bst_*` database. |

Tests and trials never run against archived databases.
