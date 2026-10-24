# -*- coding: utf-8 -*-
import math

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_is_zero

from ..lib import matching


class GstConsolidation(models.TransientModel):
    """Move a quarter's GST collected and GST paid into the GST net account, as
    one posted entry: what used to be two Manual Tax Adjustments per BAS."""
    _name = 'account.gst.consolidation'
    _description = 'GST Consolidation'

    def _default_account(self, code):
        return self.env['account.account'].search(
            [('code', '=', code), ('company_id', '=', self.env.user.company_id.id)], limit=1)

    def _default_period_end(self):
        today = fields.Date.from_string(fields.Date.context_today(self))
        return fields.Date.to_string(matching.prior_period_end(today))

    period_end = fields.Date(string='Quarter Ending', required=True, default=_default_period_end)
    name = fields.Char(string='Label', required=True)
    date = fields.Date(string='Entry Date', required=True)
    journal_id = fields.Many2one('account.journal', string='Journal', required=True,
                                 domain=[('type', '=', 'general')],
                                 default=lambda self: self.env['account.journal'].search(
                                     [('type', '=', 'general'), ('company_id', '=', self.env.user.company_id.id)],
                                     limit=1))
    collected_account_id = fields.Many2one('account.account', string='GST Collected', required=True,
                                           default=lambda self: self._default_account('21350'))
    paid_account_id = fields.Many2one('account.account', string='GST Paid', required=True,
                                      default=lambda self: self._default_account('21345'))
    net_account_id = fields.Many2one('account.account', string='GST Net', required=True,
                                     default=lambda self: self._default_account('21355'))
    currency_id = fields.Many2one('res.currency', default=lambda self: self.env.user.company_id.currency_id)
    collected_balance = fields.Monetary(string='GST Collected Balance', readonly=True)
    paid_balance = fields.Monetary(string='GST Paid Balance', readonly=True)
    collected_amount = fields.Monetary(string='Move GST Collected (BAS 1A)')
    paid_amount = fields.Monetary(string='Move GST Paid (BAS 1B)')
    net_amount = fields.Monetary(string='Net Payable (+) / Refund (-)', compute='_compute_net_amount')
    existing_move_ids = fields.Many2many('account.move', string='Already Posted', compute='_compute_existing')

    @api.depends('collected_amount', 'paid_amount')
    def _compute_net_amount(self):
        for w in self:
            w.net_amount = w.collected_amount - w.paid_amount

    @api.depends('name', 'journal_id', 'net_account_id')
    def _compute_existing(self):
        for w in self:
            w.existing_move_ids = w.name and w._existing_moves()

    def _existing_moves(self):
        """Posted entries for this label: ours carry it as the reference, the old
        Manual Tax Adjustments only as the line label on the GST net account."""
        Move = self.env['account.move']
        domain = [('state', '=', 'posted'), ('journal_id', '=', self.journal_id.id)]
        lines = self.env['account.move.line'].search(
            [('name', '=', self.name), ('account_id', '=', self.net_account_id.id)])
        return Move.search(domain + [('ref', '=', self.name)]) | Move.search(domain + [('id', 'in', lines.mapped('move_id').ids)])

    def _balance(self, account, sign):
        """Posted balance up to the quarter end; sign 1 for debit, -1 for credit."""
        if not account or not self.period_end:
            return 0.0
        self.env.cr.execute(
            """SELECT COALESCE(SUM(l.debit - l.credit), 0) FROM account_move_line l
               JOIN account_move m ON m.id = l.move_id
               WHERE l.account_id = %s AND m.state = 'posted' AND l.date <= %s""",
            (account.id, self.period_end))
        return sign * self.env.cr.fetchone()[0]

    @api.model
    def default_get(self, fields_list):
        values = super(GstConsolidation, self).default_get(fields_list)
        wizard = self.new(values)
        wizard._onchange_period()
        values.update({k: wizard[k] for k in ('name', 'date', 'collected_balance', 'paid_balance',
                                              'collected_amount', 'paid_amount')})
        return values

    @api.model
    def create(self, vals):
        """Values the form would show, for anything not given."""
        wizard = self.new(dict(self.default_get(list(self._fields)), **vals))
        wizard._onchange_period()
        for k in ('name', 'date', 'collected_balance', 'paid_balance', 'collected_amount', 'paid_amount'):
            if k not in vals:
                vals[k] = wizard[k]
        return super(GstConsolidation, self).create(vals)

    @api.onchange('period_end', 'collected_account_id', 'paid_account_id')
    def _onchange_period(self):
        if not self.period_end:
            return
        end = fields.Date.from_string(self.period_end)
        self.name = '%d Q%d GST' % (end.year, (end.month - 1) // 3 + 1)
        self.date = self.period_end
        self.collected_balance = self._balance(self.collected_account_id, -1)
        self.paid_balance = self._balance(self.paid_account_id, 1)
        # The BAS reports whole dollars, cents dropped; the cents stay behind and
        # are picked up the next quarter.
        self.collected_amount = math.floor(max(self.collected_balance, 0.0) + 1e-9)
        self.paid_amount = math.floor(max(self.paid_balance, 0.0) + 1e-9)

    @api.multi
    def action_post(self):
        self.ensure_one()
        if self._existing_moves():
            raise UserError(_("%s is already posted (%s).")
                            % (self.name, ', '.join(self._existing_moves().mapped('name'))))
        rounding = self.currency_id.rounding
        if self.collected_amount < 0 or self.paid_amount < 0:
            raise UserError(_("The amounts to move can't be negative."))
        lines = []
        if not float_is_zero(self.collected_amount, precision_rounding=rounding):
            lines += [(self.collected_account_id, self.collected_amount, 0.0),
                      (self.net_account_id, 0.0, self.collected_amount)]
        if not float_is_zero(self.paid_amount, precision_rounding=rounding):
            lines += [(self.net_account_id, self.paid_amount, 0.0),
                      (self.paid_account_id, 0.0, self.paid_amount)]
        if not lines:
            raise UserError(_("Nothing to move: both amounts are 0."))
        move = self.env['account.move'].create({
            'journal_id': self.journal_id.id,
            'date': self.date,
            'ref': self.name,
            'line_ids': [(0, 0, {'name': self.name, 'account_id': account.id,
                                 'debit': debit, 'credit': credit})
                         for account, debit, credit in lines],
        })
        move.post()
        return {
            'type': 'ir.actions.act_window',
            'name': move.name,
            'res_model': 'account.move',
            'res_id': move.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'current',
        }
