# -*- coding: utf-8 -*-
import logging
from datetime import datetime, time

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare, float_is_zero, ustr

from ..lib import matching

_logger = logging.getLogger(__name__)


class BankTxnProposalApprove(models.Model):
    """Approval: create what a proposal shows through Odoo's own methods,
    check it, and reconcile its bank lines, all or nothing per proposal."""
    _inherit = 'account.bank.txn.proposal'

    @api.multi
    def action_approve(self):
        """Approve open proposals, and failed ones again once their cause is fixed."""
        for proposal in self.filtered(lambda p: p.state != 'approved'):
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

    def _datetime(self, day):
        """Noon on this day in the user's time zone, as Odoo stores datetimes
        (UTC), so the order, receipt and transfer dates show as that day."""
        tz = pytz.timezone(self.env.context.get('tz') or self.env.user.tz or 'UTC')
        local = tz.localize(datetime.combine(fields.Date.from_string(day), time(12)))
        return fields.Datetime.to_string(local.astimezone(pytz.utc))

    def _approve_direct(self):
        """One entry per bank line, a line per proposal line on the side opposite
        the bank (a negative line, e.g. a franking credit's tax credit, on the
        bank's side); zero lines (a split part with no amount) are left out."""
        lines = self.line_ids.filtered(
            lambda l: not float_is_zero(l.price_total, precision_rounding=self.currency_id.rounding))
        moves = self.env['account.move']
        for st_line in self.statement_line_ids:
            sign = 1 if st_line.amount > 0 else -1  # credit-positive
            moves |= st_line.process_reconciliation(new_aml_dicts=[{
                'name': line.name,
                'account_id': line.account_id.id,
                'debit': max(-sign * line.price_total, 0.0),
                'credit': max(sign * line.price_total, 0.0),
            } for line in lines])
        return {'moves': moves}

    _approve_split = _approve_direct
    _approve_dividend = _approve_direct

    def _approve_sale_order(self):
        when = self._datetime(self.invoice_date)
        order = self.env['sale.order'].create({
            'partner_id': self.partner_id.id,
            'date_order': when,
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
        order.confirmation_date = when  # Odoo sets now
        # Services invoiced on delivered quantities and tracked by hand: the bank
        # payments are for services already delivered, so state them delivered.
        for line in order.order_line:
            product = line.product_id
            if product.type == 'service' and product.invoice_policy == 'delivery' \
                    and line.qty_delivered < line.product_uom_qty:
                line.qty_delivered = line.product_uom_qty
        invoice = self.env['account.invoice'].browse(order.action_invoice_create())
        return self._validate_and_reconcile(invoice, order=order)

    def _approve_purchase_order(self):
        when = self._datetime(self.invoice_date)
        order = self.env['purchase.order'].create({
            'partner_id': self.partner_id.id,
            'date_order': when,
            'txn_proposal_id': self.id,
            'order_line': [(0, 0, {
                'name': line.name,
                'product_id': line.product_id.id,
                'product_qty': line.quantity,
                'product_uom': line.product_id.uom_po_id.id,
                'price_unit': line.price_unit,
                'taxes_id': [(6, 0, line.tax_ids.ids)],
                'date_planned': when,
            }) for line in self.line_ids],
        })
        order.button_confirm()
        order.date_approve = self.invoice_date  # Odoo sets today
        for picking in order.picking_ids.filtered(lambda p: p.state not in ('done', 'cancel')):
            picking.force_assign()
            self.env['stock.immediate.transfer'].create({'pick_id': picking.id}).process()
            # Odoo stamps the transfer with now; it was received on the bill date.
            picking.move_lines.write({'date': when})
            picking.write({'date_done': when})
        # The vendor bill, built the way the "Add Purchase Order" field on a bill
        # does it. The context is the Vendor Bills menu's, so Odoo defaults to the
        # purchase journal (and BILL/ numbering), not the sales journal.
        Invoice = self.env['account.invoice'].with_context(type='in_invoice', journal_type='purchase')
        bill = Invoice.new({'type': 'in_invoice', 'partner_id': self.partner_id.id,
                            'date_invoice': self.invoice_date})
        bill._onchange_partner_id()
        bill.purchase_id = order
        bill.purchase_order_change()
        invoice = Invoice.create(bill._convert_to_write(bill._cache))
        invoice.compute_taxes()
        return self._validate_and_reconcile(invoice, order=order)

    def _validate_and_reconcile(self, invoice, order):
        """Date the invoice on the proposal's invoice date (Odoo would use today),
        validate it, and reconcile every bank line against its receivable/payable line."""
        invoice.ensure_one()
        invoice.write({'date_invoice': self.invoice_date, 'date': self.invoice_date,
                       'txn_proposal_id': self.id})
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
            order_day = fields.Datetime.context_timestamp(
                self, fields.Datetime.from_string(created['order'].date_order))
            for label, value in (('invoice date', invoice.date_invoice),
                                 ('accounting date', invoice.date),
                                 ('journal entry date', invoice.move_id.date),
                                 ('order date', fields.Date.to_string(order_day))):
                if value != self.invoice_date:
                    raise UserError(_("%s %s is not the invoice date %s") % (label, value, self.invoice_date))
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
        """Approve every open or failed proposal that isn't blocked; blocked ones are skipped."""
        proposals = self.mapped('txn_proposal_ids').filtered(
            lambda p: p.state != 'approved' and not p._get_block_reason())
        proposals.action_approve()
        return True
