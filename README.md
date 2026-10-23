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

| Target | What it does |
|---|---|
| `make pure` | Run the Odoo-free unit tests locally (`uv run pytest`). |
| `make deploy` | Copy the working tree, uncommitted changes included, to the Odoo server. |
| `make diff` | Show what `deploy` would change on the server. |
| `make test` | Deploy, then run the addon's Odoo tests in a throwaway database copy that is dropped afterwards. |
| `make upgrade DB=<db>` | Install or upgrade the addon in a trial database (only the current trial database or a `bst_*` copy). |
| `make restart` | Restart the live Odoo server so it loads new code. |

Tests and trials never run against archived databases.
