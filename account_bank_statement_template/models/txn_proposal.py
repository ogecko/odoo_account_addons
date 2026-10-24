# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare

from ..lib import matching
from .txn_template import KINDS, ORDER_KINDS


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
    attachment_ids = fields.Many2many('ir.attachment', 'account_bank_txn_proposal_attachment_rel',
                                      'proposal_id', 'attachment_id', string='Documents', copy=False,
                                      help="Supporting documents, e.g. the dividend statement. They can "
                                           "be added after approval too.")
    attachment_count = fields.Integer(string='Docs', compute='_compute_attachment_count')
    franked_amount = fields.Monetary(help="Franked part of the dividend; the rest is unfranked. "
                                          "Changing it refills the lines.")
    franking_tax_rate = fields.Float(string='Company tax rate %', readonly=True)
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
        if self.kind == 'dividend' and float_compare(
                self.franked_amount, self.bank_total, precision_rounding=self.currency_id.rounding) > 0:
            return 'franked amount above bank amount'
        if self.kind in ORDER_KINDS:
            if not self.invoice_date:
                return 'no invoice date'
            if not matching.same_quarter(dates + [fields.Date.from_string(self.invoice_date)]):
                return 'invoice date in another quarter'
        if float_compare(self.amount_total, self.bank_total,
                         precision_rounding=self.currency_id.rounding) != 0:
            return u'total %.2f ≠ bank %.2f' % (self.amount_total, self.bank_total)
        return False

    @api.depends('state', 'edited', 'date', 'invoice_date', 'franked_amount', 'line_ids.price_total',
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

    @api.depends('attachment_ids')
    def _compute_attachment_count(self):
        for p in self:
            p.attachment_count = len(p.attachment_ids)

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
    role = fields.Selection([('franked', 'Received (franked)'), ('unfranked', 'Received (unfranked)'),
                             ('fc_income', 'Franking Credit - Income'),
                             ('fc_credit', 'Franking Credit - Tax Credit')],
                            help="Which part of a dividend this line is; set from the franked amount.")
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
    def _dividend_amounts(self, bank, franked, tax_rate):
        """Line amounts by role; positive lines go on the side opposite the bank
        (credits), the franking credit's tax credit on the bank's side (debit)."""
        credit = matching.franking_credit(franked, tax_rate)
        return {'franked': franked, 'unfranked': round(bank - franked, 2),
                'fc_income': credit, 'fc_credit': -credit}

    def _apply_franked_amount(self):
        """Refill the dividend lines' amounts from the franked amount, keeping
        their descriptions and accounts."""
        amounts = self._dividend_amounts(self.bank_total, self.franked_amount, self.franking_tax_rate)
        for line in self.line_ids.filtered('role'):
            line.price_unit = amounts[line.role]

    @api.onchange('franked_amount')
    def _onchange_franked_amount(self):
        if self.kind == 'dividend':
            self._apply_franked_amount()

    @api.model
    def _build(self, statement, template, lines, fee_lines=None, fee_of=None):
        """Create the proposal for one template's matched bank lines (one line
        for direct and split templates). fee_of maps fee line id -> charge line id."""
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
        elif template.kind == 'dividend':
            prefix = describe(template.description, template.name, lines)
            accounts = {'franked': template.div_franked_account_id,
                        'unfranked': template.div_unfranked_account_id,
                        'fc_income': template.franking_income_account_id,
                        'fc_credit': template.franking_credit_account_id}
            franked = round(abs(lines.amount) * template.franked_pct / 100.0, 2)
            amounts = self._dividend_amounts(abs(lines.amount), franked, template.company_tax_rate)
            for role, label in self.env['account.bank.txn.proposal.line']._fields['role'].selection:
                vals.append({'name': '%s - %s' % (prefix, label), 'role': role,
                             'account_id': accounts[role].id, 'quantity': 1,
                             'price_unit': amounts[role], 'statement_line_ids': [(6, 0, lines.ids)]})
        elif template.kind == 'split':
            for part in template.split_ids:
                vals.append({'name': describe(part.description, part.keyword, lines),
                             'account_id': part.account_id.id, 'quantity': 1,
                             'price_unit': matching.split_amount(lines.name, part.keyword) or 0.0,
                             'statement_line_ids': [(6, 0, lines.ids)]})
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
        dividend = template.kind == 'dividend'
        return self.create({
            'franked_amount': dividend and franked or 0.0,
            'franking_tax_rate': dividend and template.company_tax_rate or 0.0,
            'statement_id': statement.id, 'template_id': template.id, 'kind': template.kind,
            'partner_id': template.partner_id.id, 'date': date,
            'statement_line_ids': [(6, 0, all_lines.ids)],
            'line_ids': [(0, 0, v) for v in vals],
        })


# Fields the owner edits on a proposal; changing them marks it edited, and they
# can't change once approved. State and links are written by matching/approval.
EDITABLE = {'partner_id', 'date', 'invoice_date', 'franked_amount', 'line_ids'}


class BankTxnProposalEditing(models.Model):
    _inherit = 'account.bank.txn.proposal'

    @api.model
    def create(self, vals):
        # Orders are invoiced on the last bank line's date unless told otherwise.
        if vals.get('kind') in ORDER_KINDS and not vals.get('invoice_date'):
            vals = dict(vals, invoice_date=vals.get('date'))
        # Lines created together with their proposal are part of building it.
        return super(BankTxnProposalEditing, self.with_context(txn_builder=True)).create(vals)

    @api.multi
    def write(self, vals):
        if EDITABLE & set(vals):
            self._check_editable()
            if not self.env.context.get('txn_builder'):
                vals = dict(vals, edited=True)
        if 'attachment_ids' in vals and not self.env.context.get('txn_builder'):
            # Documents can be added in any state; on an open proposal they also
            # keep it through Generate, like an edit.
            open_ones = self.filtered(lambda p: p.state != 'approved' and not p.edited)
            if open_ones:
                super(BankTxnProposalEditing, open_ones).write({'edited': True})
        res = super(BankTxnProposalEditing, self).write(vals)
        if 'attachment_ids' in vals:
            # Link uploads to their proposal, so they go with it if it's discarded.
            for p in self:
                p.attachment_ids.filtered(lambda a: (a.res_model, a.res_id) != (p._name, p.id)).sudo().write(
                    {'res_model': p._name, 'res_id': p.id})
        if 'franked_amount' in vals:
            for p in self.filtered(lambda p: p.kind == 'dividend'):
                p._apply_franked_amount()
        return res

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
