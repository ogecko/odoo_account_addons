# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare

from ..lib import matching
from .txn_template import KINDS


class BankTxnProposal(models.Model):
    """What a template proposes for some bank lines. A snapshot: approval
    creates what the proposal says, whatever the template says by then."""
    _name = 'account.bank.txn.proposal'
    _description = 'Bank Transaction Proposal'
    _order = 'date, id'

    statement_id = fields.Many2one('account.bank.statement', required=True, ondelete='cascade',
                                   index=True)
    template_id = fields.Many2one('account.bank.txn.template', required=True, ondelete='restrict')
    kind = fields.Selection(KINDS, required=True)
    partner_id = fields.Many2one('res.partner')
    date = fields.Date(string='Last Statement Line', required=True)
    invoice_date = fields.Date(
        help="Date of the order and its invoice or bill. Starts as the last statement line's date; "
             "a sales invoice can be dated later in the same quarter.")
    state = fields.Selection([('open', 'Open'), ('failed', 'Failed'), ('approved', 'Approved')],
                             default='open', required=True, copy=False)
    edited = fields.Boolean(copy=False)
    statement_line_ids = fields.One2many('account.bank.statement.line', 'txn_proposal_id',
                                         string='Statement Lines')
    line_ids = fields.One2many('account.bank.txn.proposal.line', 'proposal_id', string='Lines')
    currency_id = fields.Many2one('res.currency', related='statement_id.currency_id', readonly=True)
    amount_total = fields.Monetary(compute='_compute_amounts')
    bank_total = fields.Monetary(compute='_compute_amounts')
    bank_line_count = fields.Integer(string='Statement Lines', compute='_compute_amounts')
    block_reason = fields.Char(compute='_compute_block_reason')
    warning = fields.Char(compute='_compute_warning')
    status_label = fields.Char(string='Status', compute='_compute_block_reason')
    fail_reason = fields.Text(copy=False)
    sale_order_id = fields.Many2one('sale.order', copy=False, readonly=True)
    purchase_order_id = fields.Many2one('purchase.order', copy=False, readonly=True)
    invoice_id = fields.Many2one('account.invoice', copy=False, readonly=True)
    move_ids = fields.Many2many('account.move', copy=False, readonly=True)

    @api.depends('line_ids.price_total', 'statement_line_ids.amount')
    def _compute_amounts(self):
        for p in self:
            p.amount_total = sum(p.line_ids.mapped('price_total'))
            p.bank_total = sum(abs(a) for a in p.statement_line_ids.mapped('amount'))
            p.bank_line_count = len(p.statement_line_ids)

    def _get_block_reason(self):
        self.ensure_one()
        if not self.statement_line_ids:
            return 'no bank lines'
        if not self.line_ids:
            return 'no lines to create'
        if self.state != 'approved' and self.statement_line_ids.mapped('journal_entry_ids'):
            return 'bank line already reconciled'
        statement_dates = self.statement_id.line_ids.mapped('date')
        if not min(statement_dates) <= self.date <= max(statement_dates):
            return 'date outside statement period'
        dates = [fields.Date.from_string(d)
                 for d in self.statement_line_ids.mapped('date') + [self.date]]
        if not matching.same_quarter(dates):
            return 'bank lines span two quarters'
        if self.kind != 'direct':
            if not self.invoice_date:
                return 'no invoice date'
            if not matching.same_quarter(dates + [fields.Date.from_string(self.invoice_date)]):
                return 'invoice date in another quarter'
        if float_compare(self.amount_total, self.bank_total,
                         precision_rounding=self.currency_id.rounding) != 0:
            return u'total %.2f ≠ bank %.2f' % (self.amount_total, self.bank_total)
        return False

    @api.depends('state', 'edited', 'date', 'invoice_date', 'line_ids.price_total',
                 'statement_line_ids.journal_entry_ids', 'statement_line_ids.date')
    def _compute_block_reason(self):
        for p in self:
            state = p.state or 'open'  # unsaved proposals have no state yet
            p.block_reason = p._get_block_reason() if state != 'approved' and p.statement_id else False
            if state != 'open':
                p.status_label = dict(self._fields['state'].selection)[p.state]
            elif p.block_reason:
                p.status_label = 'Blocked'
            else:
                p.status_label = 'Edited' if p.edited else 'Ready'

    @api.depends('partner_id', 'kind')
    def _compute_warning(self):
        types = {'sale_order': 'out_invoice', 'purchase_order': 'in_invoice'}
        for p in self:
            if p.kind not in types or not p.partner_id:
                p.warning = False
                continue
            invoices = self.env['account.invoice'].search([
                ('partner_id', 'child_of', p.partner_id.commercial_partner_id.id),
                ('type', '=', types[p.kind]), ('state', '=', 'open')])
            p.warning = invoices and 'open invoices for %s: %s' % (
                p.partner_id.name, ', '.join(invoices.mapped('number'))) or False


class BankTxnProposalLine(models.Model):
    _name = 'account.bank.txn.proposal.line'
    _description = 'Bank Transaction Proposal Line'
    _order = 'sequence, id'

    proposal_id = fields.Many2one('account.bank.txn.proposal', required=True, ondelete='cascade')
    sequence = fields.Integer(default=10)
    name = fields.Char(string='Description', required=True)
    product_id = fields.Many2one('product.product')
    account_id = fields.Many2one('account.account')
    quantity = fields.Float(default=1.0, digits=(16, 3))
    price_unit = fields.Float(digits=(16, 2))
    tax_ids = fields.Many2many('account.tax', 'account_bank_txn_proposal_line_tax_rel',
                               'line_id', 'tax_id', string='Taxes')
    currency_id = fields.Many2one(related='proposal_id.currency_id', readonly=True)
    price_total = fields.Monetary(compute='_compute_price_total', string='Total')
    statement_line_ids = fields.Many2many('account.bank.statement.line',
                                          'account_bank_txn_proposal_line_st_line_rel',
                                          'line_id', 'statement_line_id', string='From Statement Lines')

    @api.depends('quantity', 'price_unit', 'tax_ids', 'product_id')
    def _compute_price_total(self):
        for l in self:
            l.price_total = l.tax_ids.compute_all(
                l.price_unit, l.currency_id, l.quantity, product=l.product_id,
                partner=l.proposal_id.partner_id)['total_included']


class BankTxnProposalBuilder(models.Model):
    _inherit = 'account.bank.txn.proposal'

    @api.model
    def _unit_price(self, amount, taxes):
        """Unit price that, with these taxes, gives back the bank amount."""
        excluded = taxes.filtered(lambda t: not t.price_include and t.amount_type == 'percent')
        if not excluded:
            return round(abs(amount), 2)
        return matching.ex_tax_unit(amount, sum(excluded.mapped('amount')))

    @api.model
    def _build(self, statement, template, lines, fee_lines=None, fee_of=None):
        """Create the proposal for one template's matched bank lines (one line
        for direct templates). fee_of maps fee line id -> charge line id."""
        fee_lines = fee_lines or self.env['account.bank.statement.line']
        fee_of = fee_of or {}
        all_lines = lines | fee_lines
        date = max(all_lines.mapped('date'))
        pdate = fields.Date.from_string(date)

        def describe(pattern, default, group):
            if not pattern:
                return default
            return matching.render_description(
                pattern, [fields.Date.from_string(d) for d in group.mapped('date')], pdate)

        vals = []
        if template.kind == 'direct':
            vals.append({'name': describe(template.description, lines.name, lines),
                         'account_id': template.account_id.id, 'quantity': 1,
                         'price_unit': abs(lines.amount), 'statement_line_ids': [(6, 0, lines.ids)]})
        elif template.kind == 'sale_order':
            for amount, group in matching.group_by_amount([l._to_match_dict() for l in lines]):
                group_lines = self.env['account.bank.statement.line'].browse([g['id'] for g in group])
                vals.append({
                    'name': describe(template.description, template.product_id.display_name, group_lines),
                    'product_id': template.product_id.id, 'quantity': len(group),
                    'price_unit': self._unit_price(amount, template.tax_ids),
                    'tax_ids': [(6, 0, template.tax_ids.ids)],
                    'statement_line_ids': [(6, 0, group_lines.ids)]})
        else:
            for charge in lines.sorted(lambda l: (l.date, l.id)):
                vals.append({
                    'name': describe(template.description, template.product_id.display_name, charge),
                    'product_id': template.product_id.id, 'quantity': 1,
                    'price_unit': self._unit_price(charge.amount, template.tax_ids),
                    'tax_ids': [(6, 0, template.tax_ids.ids)],
                    'statement_line_ids': [(6, 0, charge.ids)]})
                for fee in fee_lines.filtered(lambda f: fee_of.get(f.id) == charge.id):
                    product = template.companion_product_id or template.product_id
                    vals.append({
                        'name': describe(template.companion_description, product.display_name, fee),
                        'product_id': product.id, 'quantity': 1,
                        'price_unit': self._unit_price(fee.amount, template.companion_tax_ids),
                        'tax_ids': [(6, 0, template.companion_tax_ids.ids)],
                        'statement_line_ids': [(6, 0, fee.ids)]})
        for seq, v in enumerate(vals):
            v['sequence'] = seq + 1
        return self.create({
            'statement_id': statement.id, 'template_id': template.id, 'kind': template.kind,
            'partner_id': template.partner_id.id, 'date': date,
            'statement_line_ids': [(6, 0, all_lines.ids)],
            'line_ids': [(0, 0, v) for v in vals],
        })


# Fields the owner edits on a proposal; changing them marks it edited, and they
# can't change once approved. State and links are written by matching/approval.
EDITABLE = {'partner_id', 'date', 'invoice_date', 'line_ids'}


class BankTxnProposalEditing(models.Model):
    _inherit = 'account.bank.txn.proposal'

    @api.model
    def create(self, vals):
        # Orders are invoiced on the last bank line's date unless told otherwise.
        if vals.get('kind') != 'direct' and not vals.get('invoice_date'):
            vals = dict(vals, invoice_date=vals.get('date'))
        # Lines created together with their proposal are part of building it.
        return super(BankTxnProposalEditing, self.with_context(txn_builder=True)).create(vals)

    @api.multi
    def write(self, vals):
        if EDITABLE & set(vals):
            self._check_editable()
            if not self.env.context.get('txn_builder'):
                vals = dict(vals, edited=True)
        return super(BankTxnProposalEditing, self).write(vals)

    def _check_editable(self):
        if self.filtered(lambda p: p.state == 'approved'):
            raise UserError(_("An approved proposal can't be changed."))

    def _mark_edited(self):
        self._check_editable()
        if not self.env.context.get('txn_builder'):
            super(BankTxnProposalEditing, self).write({'edited': True})

    @api.multi
    def action_discard(self):
        """Drop open or failed proposals; their bank lines can be matched again."""
        if self.filtered(lambda p: p.state == 'approved'):
            raise UserError(_("An approved proposal can't be discarded."))
        self.unlink()
        return {'type': 'ir.actions.act_window_close'}

    @api.multi
    def action_open(self):
        """The proposal in an editable dialog (rows of the statement's Proposals
        tab open this, see static/src/js). Closing it reloads the statement."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Proposal'),
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'views': [(self.env.ref('account_bank_statement_template.view_bank_txn_proposal_form').id, 'form')],
            'target': 'new',
        }

    @api.multi
    def action_save(self):
        """The dialog's Save: the form saves before calling this."""
        return {'type': 'ir.actions.act_window_close'}


class BankTxnProposalLineEditing(models.Model):
    _inherit = 'account.bank.txn.proposal.line'

    @api.model
    def create(self, vals):
        line = super(BankTxnProposalLineEditing, self).create(vals)
        line.proposal_id._mark_edited()
        return line

    @api.multi
    def write(self, vals):
        self.mapped('proposal_id')._mark_edited()
        return super(BankTxnProposalLineEditing, self).write(vals)

    @api.multi
    def unlink(self):
        self.mapped('proposal_id')._mark_edited()
        return super(BankTxnProposalLineEditing, self).unlink()
