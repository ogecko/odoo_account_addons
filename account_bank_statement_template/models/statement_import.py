# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.tools import float_compare


class AccountBankStatement(models.Model):
    _inherit = 'account.bank.statement'

    txn_bank_balance = fields.Monetary(
        string='Bank Balance in File', readonly=True,
        help="The balance in the imported file. Banks give it as at the day the file "
             "was downloaded, so it includes anything after the statement's last line.")

    def _txn_previous_statement(self):
        """The journal's statement whose last line comes before this one's first."""
        self.ensure_one()
        first = min(self.line_ids.mapped('date'))
        candidates = self.search([('journal_id', '=', self.journal_id.id), ('id', '!=', self.id)])
        earlier = [(max(s.line_ids.mapped('date')), s.id, s) for s in candidates
                   if s.line_ids and max(s.line_ids.mapped('date')) < first]
        return max(earlier)[2] if earlier else self.browse()


class AccountBankStatementImport(models.TransientModel):
    _inherit = 'account.bank.statement.import'

    def _create_bank_statements(self, stmts_vals):
        """Balances follow on from the previous statement: start = its ending
        balance, end = start + the imported lines. The file's own balance is
        kept in txn_bank_balance for checking."""
        statement_ids, notifications = super(AccountBankStatementImport, self)._create_bank_statements(stmts_vals)
        for st in self.env['account.bank.statement'].browse(statement_ids):
            st.txn_bank_balance = st.balance_end_real
            previous = st._txn_previous_statement()
            if not previous:
                continue
            start = previous.balance_end_real
            st.write({'balance_start': start,
                      'balance_end_real': start + sum(st.line_ids.mapped('amount'))})
            if float_compare(st.balance_end_real, st.txn_bank_balance,
                             precision_rounding=st.currency_id.rounding) != 0:
                notifications.append({
                    'type': 'info',
                    'message': _("Starting balance %.2f taken from statement %s (%s). The bank's "
                                 "balance in the file, %.2f, includes transactions after %s.")
                               % (start, previous.name, previous.date, st.txn_bank_balance,
                                  max(st.line_ids.mapped('date'))),
                })
        return statement_ids, notifications

