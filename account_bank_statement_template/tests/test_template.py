# -*- coding: utf-8 -*-
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase


class TestTemplate(TransactionCase):

    def setUp(self):
        super(TestTemplate, self).setUp()
        self.partner = self.env['res.partner'].create({'name': 'Test Vendor'})
        self.product = self.env['product.product'].create({'name': 'Test Service', 'type': 'service'})
        self.account = self.env['account.account'].create({
            'code': 'TXN810', 'name': 'Test Interest Income',
            'user_type_id': self.env.ref('account.data_account_type_revenue').id})
        self.tax = self.env['account.tax'].create({
            'name': 'T Purch Inc 10%', 'type_tax_use': 'purchase', 'amount': 10.0,
            'price_include': True})
        self.Template = self.env['account.bank.txn.template']

    def _vals(self, **kw):
        vals = {'name': 'Interest', 'kind': 'direct', 'direction': 'in',
                'match_text': 'credit interest', 'amount_min': 0.0, 'amount_max': 500.0,
                'account_id': self.account.id}
        vals.update(kw)
        return vals

    def _order_vals(self, **kw):
        vals = self._vals(kind='purchase_order', direction='out', account_id=False,
                          partner_id=self.partner.id, product_id=self.product.id)
        vals.update(kw)
        return vals

    def test_create_direct_template(self):
        t = self.Template.create(self._vals())
        self.assertTrue(t.active)
        self.assertEqual(t.fee_pct_min, 2.0)
        self.assertEqual(t.fee_pct_max, 4.0)

    def test_order_template_needs_partner_and_product(self):
        with self.assertRaises(ValidationError):
            self.Template.create(self._order_vals(product_id=False))
        with self.assertRaises(ValidationError):
            self.Template.create(self._order_vals(partner_id=False))

    def test_direct_template_needs_account(self):
        with self.assertRaises(ValidationError):
            self.Template.create(self._vals(account_id=False))

    def test_direct_template_rejects_tax(self):
        with self.assertRaises(ValidationError):
            self.Template.create(self._vals(tax_ids=[(6, 0, [self.tax.id])]))

    def test_negative_min_rejected(self):
        with self.assertRaises(ValidationError):
            self.Template.create(self._vals(amount_min=-10.0))

    def test_min_above_max_rejected(self):
        with self.assertRaises(ValidationError):
            self.Template.create(self._vals(amount_min=50.0, amount_max=40.0))

    def test_invalid_regex_rejected(self):
        with self.assertRaises(ValidationError):
            self.Template.create(self._vals(match_is_regex=True, match_text='('))

    def test_companion_only_on_orders(self):
        with self.assertRaises(ValidationError):
            self.Template.create(self._vals(companion_text='international transaction fee'))

    def test_to_match_dict(self):
        t = self.Template.create(self._order_vals(
            name='Mailgun', match_text='sinch mailgun', amount_min=0.5, amount_max=100.0,
            companion_text='international transaction fee', companion_product_id=self.product.id))
        self.assertEqual(t._to_match_dict(), {
            'id': t.id, 'name': 'Mailgun', 'direction': 'out',
            'match_text': 'sinch mailgun', 'match_is_regex': False,
            'amount_min': 0.5, 'amount_max': 100.0,
            'companion_text': 'international transaction fee', 'companion_is_regex': False,
            'fee_pct_min': 2.0, 'fee_pct_max': 4.0})
