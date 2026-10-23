# -*- coding: utf-8 -*-
from datetime import date

from odoo import fields
from odoo.tests.common import TransactionCase


class TxnCase(TransactionCase):
    """Made-up partners, products, accounts, taxes and a bank journal, so tests
    never depend on the real data in the database they run in."""

    def setUp(self):
        super(TxnCase, self).setUp()
        ref = self.env.ref
        Account = self.env['account.account']
        self.income = Account.create({
            'code': 'TXN410', 'name': 'Test Income',
            'user_type_id': ref('account.data_account_type_revenue').id})
        self.interest_income = Account.create({
            'code': 'TXN810', 'name': 'Test Interest Income',
            'user_type_id': ref('account.data_account_type_revenue').id})
        self.expense = Account.create({
            'code': 'TXN610', 'name': 'Test Expense',
            'user_type_id': ref('account.data_account_type_expenses').id})
        self.gst = Account.create({
            'code': 'TXN220', 'name': 'Test GST',
            'user_type_id': ref('account.data_account_type_current_liabilities').id})
        self.journal = self.env['account.journal'].create({
            'name': 'Test Bank', 'code': 'TXNB', 'type': 'bank'})
        Tax = self.env['account.tax']
        self.sale_tax = Tax.create({
            'name': 'T Sale 10%', 'type_tax_use': 'sale', 'amount': 10.0,
            'price_include': False, 'account_id': self.gst.id, 'refund_account_id': self.gst.id})
        self.purch_tax_inc = Tax.create({
            'name': 'T Purch Inc 10%', 'type_tax_use': 'purchase', 'amount': 10.0,
            'price_include': True, 'account_id': self.gst.id, 'refund_account_id': self.gst.id})
        self.customer = self.env['res.partner'].create({'name': 'Test Customer', 'customer': True})
        self.vendor = self.env['res.partner'].create({'name': 'Test Vendor', 'supplier': True})
        Product = self.env['product.product']
        self.service = Product.create({
            'name': 'Test Service', 'type': 'service', 'invoice_policy': 'order',
            'purchase_method': 'purchase', 'taxes_id': [(6, 0, [])],
            'supplier_taxes_id': [(6, 0, [])],
            'property_account_income_id': self.income.id,
            'property_account_expense_id': self.expense.id})
        self.consumable = Product.create({
            'name': 'Test Consumable', 'type': 'consu', 'invoice_policy': 'order',
            'purchase_method': 'purchase', 'taxes_id': [(6, 0, [])],
            'supplier_taxes_id': [(6, 0, [])],
            'property_account_income_id': self.income.id,
            'property_account_expense_id': self.expense.id})

    def make_statement(self, lines):
        """lines: [(date, amount, name), ...]"""
        return self.env['account.bank.statement'].create({
            'journal_id': self.journal.id,
            'date': fields.Date.to_string(max(l[0] for l in lines)),
            'line_ids': [(0, 0, {'date': fields.Date.to_string(d), 'amount': a, 'name': n})
                         for d, a, n in lines],
        })

    def make_template(self, **vals):
        defaults = {'name': 'Test template', 'kind': 'direct', 'direction': 'in',
                    'match_text': 'test', 'amount_min': 0.0, 'amount_max': 1000.0,
                    'account_id': self.interest_income.id}
        defaults.update(vals)
        return self.env['account.bank.txn.template'].create(defaults)


D = date
