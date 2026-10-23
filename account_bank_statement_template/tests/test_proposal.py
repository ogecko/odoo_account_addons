# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import fields

from .common import TxnCase, D


class TestProposal(TxnCase):

    def setUp(self):
        super(TestProposal, self).setUp()
        self.sale_template = self.make_template(
            name='Weekly', kind='sale_order', match_text='weekly', account_id=False,
            partner_id=self.customer.id, product_id=self.service.id,
            tax_ids=[(6, 0, [self.sale_tax.id])])

    def _proposal(self, statement, bank_lines, order_lines, kind='sale_order', partner=None,
                  date=None):
        return self.env['account.bank.txn.proposal'].create({
            'statement_id': statement.id,
            'template_id': self.sale_template.id,
            'kind': kind,
            'partner_id': (partner or self.customer).id,
            'date': fields.Date.to_string(date or max(
                fields.Date.from_string(l.date) for l in bank_lines)),
            'statement_line_ids': [(6, 0, bank_lines.ids)],
            'line_ids': [(0, 0, dict({'name': 'Weekly Service', 'product_id': self.service.id,
                                      'tax_ids': [(6, 0, [self.sale_tax.id])]}, **ol))
                         for ol in order_lines],
        })

    def _weekly(self, n, amount, start=D(2026, 7, 2)):
        return self.make_statement([(start + timedelta(weeks=i), amount, 'Weekly %d' % i)
                                    for i in range(n)])

    def test_amount_total_uses_odoo_tax_compute(self):
        st = self._weekly(12, 44.00)
        p = self._proposal(st, st.line_ids, [{'quantity': 12, 'price_unit': 40.0}])
        self.assertAlmostEqual(p.amount_total, 528.00, places=2)
        self.assertAlmostEqual(p.bank_total, 528.00, places=2)

    def test_ready_when_totals_tie(self):
        st = self._weekly(12, 44.00)
        p = self._proposal(st, st.line_ids, [{'quantity': 12, 'price_unit': 40.0}])
        self.assertFalse(p.block_reason)
        self.assertEqual(p.status_label, 'Ready')

    def test_blocked_on_total_mismatch(self):
        st = self._weekly(1, 44.05)
        p = self._proposal(st, st.line_ids, [{'quantity': 1, 'price_unit': 40.0}])
        self.assertTrue(p.block_reason.startswith(u'total 44.00'), p.block_reason)
        self.assertEqual(p.status_label, 'Blocked')

    def test_blocked_when_date_outside_statement_period(self):
        st = self._weekly(2, 44.00)
        p = self._proposal(st, st.line_ids, [{'quantity': 2, 'price_unit': 40.0}],
                           date=D(2026, 9, 30))
        self.assertEqual(p.block_reason, 'date outside statement period')

    def test_blocked_when_lines_span_two_quarters(self):
        st = self.make_statement([(D(2026, 6, 30), 44.00, 'Weekly a'), (D(2026, 7, 1), 44.00, 'Weekly b')])
        p = self._proposal(st, st.line_ids, [{'quantity': 2, 'price_unit': 40.0}])
        self.assertEqual(p.block_reason, 'bank lines span two quarters')

    def test_blocked_when_bank_line_reconciled(self):
        st = self._weekly(1, 44.00)
        p = self._proposal(st, st.line_ids, [{'quantity': 1, 'price_unit': 40.0}])
        st.line_ids.process_reconciliation(new_aml_dicts=[{
            'name': 'by hand', 'account_id': self.income.id, 'debit': 0.0, 'credit': 44.00}])
        p.invalidate_cache()
        self.assertEqual(p.block_reason, 'bank line already reconciled')

    def test_warning_lists_open_invoice_for_partner(self):
        bill = self.env['account.invoice'].create({
            'partner_id': self.vendor.id, 'type': 'in_invoice',
            'invoice_line_ids': [(0, 0, {'name': 'old', 'account_id': self.expense.id,
                                         'quantity': 1, 'price_unit': 66.66})]})
        bill.action_invoice_open()
        st = self.make_statement([(D(2026, 7, 6), -110.0, 'AWS')])
        p = self._proposal(st, st.line_ids,
                           [{'quantity': 1, 'price_unit': 110.0,
                             'tax_ids': [(6, 0, [self.purch_tax_inc.id])]}],
                           kind='purchase_order', partner=self.vendor)
        self.assertIn(bill.number, p.warning)
