# The pure library is imported directly, not via the Odoo addon package,
# whose __init__ imports odoo.
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'account_bank_statement_template', 'lib'))
