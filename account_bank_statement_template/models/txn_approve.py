# -*- coding: utf-8 -*-
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare, ustr

from ..lib import matching

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

    def _approve_sale_order(self):
        order = self.env['sale.order'].create({
            'partner_id': self.partner_id.id,
            'date_order': self.date,
            'txn_proposal_id': self.id,
            'order_line': [(0, 0, {
                'name': line.name,
                'product_id': line.product_id.id,
                'product_uom_qty': line.quantity,
                'price_unit': line.price_unit,
                'tax_id': [(6, 0, line.tax_ids.ids)],
            }) for line in self.line_ids],
        })
        order.action_confirm()
        invoice = self.env['account.invoice'].browse(order.action_invoice_create())
        return self._validate_and_reconcile(invoice, order=order)

    def _validate_and_reconcile(self, invoice, order):
        """Date the invoice on the proposal date (Odoo would use today), validate
        it, and reconcile every bank line against its receivable/payable line."""
        invoice.ensure_one()
        invoice.write({'date_invoice': self.date, 'date': self.date, 'txn_proposal_id': self.id})
        invoice.action_invoice_open()
        open_line = invoice.move_id.line_ids.filtered(lambda l: l.account_id == invoice.account_id)
        moves = self.env['account.move']
        for st_line in self.statement_line_ids.sorted(lambda l: (l.date, l.id)):
            money_in = st_line.amount > 0
            moves |= st_line.process_reconciliation(counterpart_aml_dicts=[{
                'move_line': open_line,
                'name': invoice.number,
                'debit': 0.0 if money_in else abs(st_line.amount),
                'credit': abs(st_line.amount) if money_in else 0.0,
            }])
        return {'order': order, 'invoice': invoice, 'moves': moves | invoice.move_id}

    def _post_check(self, created):
        """Raise UserError unless what was created is complete and correctly dated."""
        invoice = created.get('invoice')
        if invoice:
            if invoice.state != 'paid':
                raise UserError(_("invoice %s is %s, not paid") % (invoice.number, invoice.state))
            if float_compare(invoice.amount_total, self.bank_total,
                             precision_rounding=self.currency_id.rounding) != 0:
                raise UserError(_("invoice total %.2f differs from bank total %.2f")
                                % (invoice.amount_total, self.bank_total))
            for label, value in (('invoice date', invoice.date_invoice),
                                 ('accounting date', invoice.date),
                                 ('journal entry date', invoice.move_id.date)):
                if value != self.date:
                    raise UserError(_("%s %s is not the proposal date %s") % (label, value, self.date))
            dates = [fields.Date.from_string(d) for d in created['moves'].mapped('date')]
            if not matching.same_quarter(dates):
                raise UserError(_("entries span two quarters"))
        for st_line in self.statement_line_ids:
            if not st_line.journal_entry_ids:
                raise UserError(_("bank line %s was not reconciled") % st_line.name)
            for move in st_line.journal_entry_ids:
                if move.date != st_line.date:
                    raise UserError(_("entry %s is dated %s, bank line %s") % (move.name, move.date, st_line.date))

    def _link(self, created):
        moves = created.get('moves', self.env['account.move'])
        moves.write({'txn_proposal_id': self.id})
        vals = {'move_ids': [(6, 0, moves.ids)]}
        if created.get('invoice'):
            vals['invoice_id'] = created['invoice'].id
        order = created.get('order')
        if order and order._name == 'sale.order':
            vals['sale_order_id'] = order.id
        elif order:
            vals['purchase_order_id'] = order.id
        self.write(vals)


class AccountBankStatementApprove(models.Model):
    _inherit = 'account.bank.statement'

    @api.multi
    def action_approve_ready(self):
        """Approve every open proposal that isn't blocked; blocked ones are skipped."""
        proposals = self.mapped('txn_proposal_ids').filtered(
            lambda p: p.state == 'open' and not p._get_block_reason())
        proposals.action_approve()
        return True
