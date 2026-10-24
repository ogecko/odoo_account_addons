# -*- coding: utf-8 -*-
import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

KINDS = [
    ('direct', 'Direct to account'),
    ('sale_order', 'Sales order'),
    ('purchase_order', 'Purchase order'),
    ('split', 'Split to accounts'),
    ('dividend', 'Dividend'),
]
# Kinds that post straight to accounts (one proposal per bank line), and kinds that make orders.
ENTRY_KINDS = ('direct', 'split', 'dividend')
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
        help="Placeholders: {year} {first_week} {last_week} {quarter} {date} {period_end} {prior_period_end}")
    # Dividends: bank amount = franked + unfranked; franking credit on the franked part
    franked_pct = fields.Float(string='Franked %', default=100.0,
                               help="Share of a dividend that is franked, to start with; "
                                    "the proposal's franked amount can be changed.")
    company_tax_rate = fields.Float(string='Company tax rate %', default=30.0,
                                    help="The paying company's tax rate, for its franking credits.")
    div_franked_account_id = fields.Many2one('account.account', string='Received (franked)')
    div_unfranked_account_id = fields.Many2one('account.account', string='Received (unfranked)')
    franking_income_account_id = fields.Many2one('account.account', string='Franking credit income')
    franking_credit_account_id = fields.Many2one('account.account', string='Franking credit tax credit')
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
                    'split_ids', 'div_franked_account_id', 'div_unfranked_account_id',
                    'franking_income_account_id', 'franking_credit_account_id',
                    'franked_pct', 'company_tax_rate')
    def _check_kind_fields(self):
        for t in self:
            if t.kind == 'dividend':
                if not (t.div_franked_account_id and t.div_unfranked_account_id
                        and t.franking_income_account_id and t.franking_credit_account_id):
                    raise ValidationError(_("Template '%s' books dividends: set the four dividend accounts.") % t.name)
                if not 0 <= t.franked_pct <= 100 or not 0 <= t.company_tax_rate < 100:
                    raise ValidationError(_("Template '%s': franked %% and tax rate are percentages.") % t.name)
                if t.tax_ids or t.companion_text or t.direction != 'in':
                    raise ValidationError(_("Template '%s': dividends are money in, with no tax or fees.") % t.name)
            elif t.kind == 'split':
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
    def unlink(self):
        """Open and failed proposals go with their template; approved ones are
        the record of what it created, so their template can only be archived."""
        proposals = self.env['account.bank.txn.proposal'].search([('template_id', 'in', self.ids)])
        approved = proposals.filtered(lambda p: p.state == 'approved')
        if approved:
            lines = []
            for t in approved.mapped('template_id'):
                statements = approved.filtered(lambda p: p.template_id == t).mapped('statement_id')
                lines.append(_("Template '%s' has approved proposals (statement %s).")
                             % (t.name, ', '.join(st.name or '%s %s' % (st.journal_id.name, st.date) for st in statements)))
            raise UserError('\n'.join(lines) + '\n' + _("Archive it instead: Action > Archive."))
        proposals.unlink()
        return super(BankTxnTemplate, self).unlink()

    # Templates move between databases (a trial and live) by Odoo's export and
    # import. Linked records are written by name, or by a real external id
    # (e.g. l10n_au taxes and accounts), never by the '__export__' ids Odoo
    # makes up, which exist only in the database exported from.
    NAME_KEYS = {'account.account': 'code', 'account.journal': 'name'}

    def _field_at(self, path):
        model, field = self, None
        for part in path.split('/'):
            field = model._fields.get(part) if model is not None else None
            model = self.env[field.comodel_name] if field is not None and field.relational else None
        return field

    def _portable_ref(self, record):
        data = self.env['ir.model.data'].search(
            [('model', '=', record._name), ('res_id', '=', record.id), ('module', '!=', '__export__')], limit=1)
        if data:
            return '%s.%s' % (data.module, data.name)
        return record[self.NAME_KEYS[record._name]] if record._name in self.NAME_KEYS else record.display_name

    def _resolve_ref(self, comodel, value, row):
        """Database id of a record written as an external id or a name."""
        if '.' in value:
            record = self.env.ref(value, raise_if_not_found=False)
            if record and record._name == comodel:
                return record.id
        Model = self.env[comodel]
        key = self.NAME_KEYS.get(comodel)
        found = Model.search([(key, '=', value)]) if key else Model.browse()
        if not found:
            found = Model.browse([r[0] for r in Model.name_search(value, operator='ilike', limit=None)])
            found = found.filtered(lambda r: r.display_name == value or
                                   (key and r[key] == value) or r.display_name.split(' (')[0] == value)
        if len(found) != 1:
            raise UserError(_("Row %d: %s %s named '%s'.") % (
                row, 'no' if not found else 'several', Model._description, value))
        return found.id

    @api.multi
    def export_data(self, fields_to_export, raw_data=False):
        paths, swap = list(fields_to_export), {}
        for i, path in enumerate(paths):
            if path.endswith('/id'):
                field = self._field_at(path[:-3])
                if field is not None and field.type in ('many2one', 'many2many'):
                    paths[i] = path[:-3] + '/.id'
                    swap[i] = field.comodel_name
        result = super(BankTxnTemplate, self).export_data(paths, raw_data)
        for row in result['datas']:
            for i, comodel in swap.items():
                ids = [int(x) for x in str(row[i] or '').split(',') if x]
                row[i] = ','.join(self._portable_ref(r) for r in self.env[comodel].browse(ids))
        return result

    @api.model
    def load(self, fields, data):
        fields, data = list(fields), [list(row) for row in data]
        for i, path in enumerate(fields):
            if path.endswith('/id'):
                field = self._field_at(path[:-3])
                if field is None or field.type not in ('many2one', 'many2many'):
                    continue
                for n, row in enumerate(data, 2):
                    values = [v.strip() for v in (row[i] or '').split(',') if v.strip()]
                    if field.type == 'many2one':
                        values = [row[i].strip()] if (row[i] or '').strip() else []
                    row[i] = ','.join(str(self._resolve_ref(field.comodel_name, v, n)) for v in values)
                fields[i] = path[:-3] + '/.id'
            else:
                # Without "Import-Compatible Export", links are display names
                # ('81000 Interest Income', 'Bank (AUD)').
                field = self._field_at(path)
                if field is not None and field.type == 'many2one' and field.comodel_name in self.NAME_KEYS:
                    key = self.NAME_KEYS[field.comodel_name]
                    names = {r.display_name: r[key] for r in self.env[field.comodel_name].search([])}
                    for row in data:
                        row[i] = names.get(row[i], row[i])
        # Re-importing an export updates the templates by id; their split lines
        # are replaced by the file's, as split line ids from another database
        # can't be found.
        if 'split_ids/id' in fields:
            i = fields.index('split_ids/id')
            fields = fields[:i] + fields[i + 1:]
            data = [row[:i] + row[i + 1:] for row in data]
        if 'id' in fields and any(f.startswith('split_ids/') for f in fields):
            i = fields.index('id')
            existing = self.browse()
            for row in data:
                xmlid = row[i] if '.' in (row[i] or '') else row[i] and '__import__.' + row[i]
                record = xmlid and self.env.ref(xmlid, raise_if_not_found=False)
                if record and record._name == self._name:
                    existing |= record
            existing.mapped('split_ids').unlink()
        return super(BankTxnTemplate, self).load(fields, data)

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
                              help="Placeholders as on the template, e.g. GST period ending {prior_period_end}")
