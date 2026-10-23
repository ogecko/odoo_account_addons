# -*- coding: utf-8 -*-
import logging

from odoo import _, api, models
from odoo.exceptions import UserError
from odoo.tools import ustr

_logger = logging.getLogger(__name__)


class BankTxnProposalApprove(models.Model):
    """Approval: create what a proposal shows through Odoo's own methods,
    check it, and reconcile its bank lines, all or nothing per proposal."""
    _inherit = 'account.bank.txn.proposal'

    @api.multi
    def action_approve(self):
        for proposal in self.filtered(lambda p: p.state == 'open'):
            reason = proposal._get_block_reason()
            if reason:
                raise UserError(_("%s: %s") % (proposal.template_id.name, reason))
            proposal._approve_one()
        return True

    def _approve_one(self):
        self.ensure_one()
        try:
            with self.env.cr.savepoint():
                if self.statement_line_ids.mapped('journal_entry_ids'):
                    raise UserError(_("bank line already reconciled"))
                created = getattr(self, '_approve_%s' % self.kind)()
                self._post_check(created)
                self._link(created)
                self.write({'state': 'approved', 'fail_reason': False})
        except Exception as e:  # noqa: B902 - any failure leaves this proposal failed, others go on
            _logger.warning("Approval of proposal %s (%s) failed", self.id, self.template_id.name, exc_info=True)
            self.env.invalidate_all()
            self.write({'state': 'failed', 'fail_reason': getattr(e, 'name', None) or ustr(e)})

    def _approve_direct(self):
        moves = self.env['account.move']
        for st_line in self.statement_line_ids:
            money_in = st_line.amount > 0
            moves |= st_line.process_reconciliation(new_aml_dicts=[{
                'name': line.name,
                'account_id': line.account_id.id,
                'debit': 0.0 if money_in else line.price_total,
                'credit': line.price_total if money_in else 0.0,
            } for line in self.line_ids])
        return {'moves': moves}

    def _post_check(self, created):
        """Raise UserError unless what was created is complete and correctly dated."""
        for st_line in self.statement_line_ids:
            if not st_line.journal_entry_ids:
                raise UserError(_("bank line %s was not reconciled") % st_line.name)
            for move in st_line.journal_entry_ids:
                if move.date != st_line.date:
                    raise UserError(_("entry %s is dated %s, bank line %s") % (move.name, move.date, st_line.date))

    def _link(self, created):
        moves = created.get('moves', self.env['account.move'])
        moves.write({'txn_proposal_id': self.id})
        self.move_ids = [(6, 0, moves.ids)]


class AccountBankStatementApprove(models.Model):
    _inherit = 'account.bank.statement'

    @api.multi
    def action_approve_ready(self):
        """Approve every open proposal that isn't blocked; blocked ones are skipped."""
        proposals = self.mapped('txn_proposal_ids').filtered(
            lambda p: p.state == 'open' and not p._get_block_reason())
        proposals.action_approve()
        return True
