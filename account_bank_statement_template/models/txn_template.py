# -*- coding: utf-8 -*-
import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

KINDS = [
    ('direct', 'Direct to account'),
    ('sale_order', 'Sales order'),
    ('purchase_order', 'Purchase order'),
    ('split', 'Split to accounts'),
]
# Kinds that post straight to accounts (one proposal per bank line), and kinds that make orders.
ENTRY_KINDS = ('direct', 'split')
ORDER_KINDS = ('sale_order', 'purchase_order')


class BankTxnTemplate(models.Model):
    _name = 'account.bank.txn.template'
    _description = 'Bank Transaction Template'
    _order = 'sequence, name'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    kind = fields.Selection(KINDS, required=True, default='direct')
    journal_id = fields.Many2one(
        'account.journal', string='Bank journal', domain=[('type', '=', 'bank')],
        help="Only match lines of this journal's statements. Empty: any bank journal.")

    # Matching
    direction = fields.Selection(
        [('in', 'Money in'), ('out', 'Money out')], required=True, default='in')
    match_text = fields.Char(
        required=True, help="Text to find in the bank line's description "
                            "(case and spacing ignored), or a regular expression.")
    match_is_regex = fields.Boolean(string='Regular expression')
    amount_min = fields.Float(string='Min amount', digits=(16, 2), required=True, default=0.0)
    amount_max = fields.Float(string='Max amount', digits=(16, 2), required=True, default=0.0)

    # What it creates
    partner_id = fields.Many2one('res.partner', string='Partner')
    product_id = fields.Many2one('product.product', string='Product')
    account_id = fields.Many2one('account.account', string='Account')
    tax_ids = fields.Many2many(
        'account.tax', 'account_bank_txn_template_tax_rel', 'template_id', 'tax_id',
        string='Taxes')
    description = fields.Char(
        string='Line description',
        help="Placeholders: {year} {first_week} {last_week} {quarter} {date} {period_end}")
    split_ids = fields.One2many('account.bank.txn.template.split', 'template_id', string='Split',
                                copy=True)

    # Fees that belong to a matched charge (e.g. international transaction fees)
    companion_text = fields.Char(string='Fee text')
    companion_is_regex = fields.Boolean(string='Fee text is a regular expression')
    companion_product_id = fields.Many2one('product.product', string='Fee product')
    companion_description = fields.Char(string='Fee line description')
    companion_tax_ids = fields.Many2many(
        'account.tax', 'account_bank_txn_template_fee_tax_rel', 'template_id', 'tax_id',
        string='Fee taxes')
    fee_pct_min = fields.Float(string='Fee min %', default=2.0)
    fee_pct_max = fields.Float(string='Fee max %', default=4.0)

    @api.constrains('kind', 'partner_id', 'product_id', 'account_id', 'tax_ids', 'companion_text',
                    'split_ids')
    def _check_kind_fields(self):
        for t in self:
            if t.kind == 'split':
                if not t.split_ids:
                    raise ValidationError(_("Template '%s' splits to accounts: add the split lines.") % t.name)
                if t.tax_ids or t.companion_text:
                    raise ValidationError(_("Template '%s': split entries carry no tax or fees.") % t.name)
            elif t.kind == 'direct':
                if not t.account_id:
                    raise ValidationError(_("Template '%s' posts to an account: set the account.") % t.name)
                if t.tax_ids:
                    raise ValidationError(_("Template '%s': direct entries carry no tax.") % t.name)
                if t.companion_text:
                    raise ValidationError(_("Template '%s': fees can only be added to orders.") % t.name)
            elif not (t.partner_id and t.product_id):
                raise ValidationError(_("Template '%s' creates orders: set the partner and product.") % t.name)

    @api.constrains('amount_min', 'amount_max')
    def _check_range(self):
        for t in self:
            if t.amount_min < 0 or t.amount_max < 0:
                raise ValidationError(_("Template '%s': amounts are positive; direction says in or out.") % t.name)
            if t.amount_min > t.amount_max:
                raise ValidationError(_("Template '%s': min amount is above max amount.") % t.name)

    @api.constrains('match_text', 'match_is_regex', 'companion_text', 'companion_is_regex')
    def _check_regex(self):
        for t in self:
            for text, is_regex in ((t.match_text, t.match_is_regex),
                                   (t.companion_text, t.companion_is_regex)):
                if text and is_regex:
                    try:
                        re.compile(text)
                    except re.error as e:
                        raise ValidationError(_("Template '%s': invalid regular expression '%s' (%s)")
                                              % (t.name, text, e))

    @api.multi
    def _to_match_dict(self):
        self.ensure_one()
        return {
            'id': self.id, 'name': self.name, 'direction': self.direction,
            'match_text': self.match_text, 'match_is_regex': self.match_is_regex,
            'amount_min': self.amount_min, 'amount_max': self.amount_max,
            'companion_text': self.companion_text or None,
            'companion_is_regex': self.companion_is_regex,
            'fee_pct_min': self.fee_pct_min, 'fee_pct_max': self.fee_pct_max,
        }


class BankTxnTemplateSplit(models.Model):
    """One part of a split template's entry: the amount after its keyword in
    the bank description (0 if absent) goes to its account."""
    _name = 'account.bank.txn.template.split'
    _description = 'Bank Transaction Template Split Line'
    _order = 'sequence, id'

    template_id = fields.Many2one('account.bank.txn.template', required=True, ondelete='cascade')
    sequence = fields.Integer(default=10)
    keyword = fields.Char(required=True, help="The amount follows this word in the bank "
                                             "description, e.g. GST in 'GST 12 PAYG 300'.")
    account_id = fields.Many2one('account.account', string='Account', required=True)
    description = fields.Char(string='Line description',
                              help="Placeholders as on the template, e.g. GST period ending {period_end}")
