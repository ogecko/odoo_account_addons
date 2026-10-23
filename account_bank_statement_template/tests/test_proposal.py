# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError

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


class TestProposalEditing(TxnCase):

    def setUp(self):
        super(TestProposalEditing, self).setUp()
        self.aws_t = self.make_template(
            name='AWS', kind='purchase_order', direction='out', match_text='amazon web services',
            amount_min=10.0, amount_max=900.0, account_id=False,
            partner_id=self.vendor.id, product_id=self.consumable.id,
            tax_ids=[(6, 0, [self.purch_tax_inc.id])], description='Web Services')
        self.st = self.make_statement([(D(2026, 7, 6), -111.11, 'AMAZON WEB SERVICES'),
                                       (D(2026, 8, 4), -222.22, 'AMAZON WEB SERVICES')])
        self.st.action_match_templates()
        self.p = self.st.txn_proposal_ids

    def test_edit_line_marks_edited(self):
        self.assertFalse(self.p.edited)
        self.p.line_ids[0].name = 'Web Services (July)'
        self.assertTrue(self.p.edited)
        self.assertEqual(self.p.status_label, 'Edited')

    def test_edit_header_marks_edited(self):
        self.p.date = '2026-08-03'
        self.assertTrue(self.p.edited)

    def test_builder_context_does_not_mark_edited(self):
        self.p.with_context(txn_builder=True).line_ids[0].name = 'x'
        self.assertFalse(self.p.edited)

    def test_approval_does_not_mark_edited(self):
        self.p.action_approve()
        self.assertEqual(self.p.state, 'approved', self.p.fail_reason)
        self.assertFalse(self.p.edited)

    def test_cannot_edit_approved(self):
        self.p.action_approve()
        with self.assertRaises(UserError):
            self.p.line_ids[0].name = 'late change'
        with self.assertRaises(UserError):
            self.p.partner_id = self.customer

    def test_remove_bank_line_sticks_across_rerun(self):
        july = self.st.line_ids.sorted('date')[0]
        july.action_txn_set_manual()
        self.assertFalse(july.txn_proposal_id)
        self.assertEqual(july.txn_manual_reason, 'removed by owner')
        self.assertEqual([l.price_unit for l in self.p.line_ids], [222.22])
        self.assertFalse(self.p.block_reason, self.p.block_reason)
        self.assertTrue(self.p.edited)
        self.st.action_match_templates()
        self.assertFalse(july.txn_proposal_id)
        self.assertEqual(july.txn_manual_reason, 'removed by owner')

    def test_clear_manual_rematches(self):
        july = self.st.line_ids.sorted('date')[0]
        july.action_txn_set_manual()
        july.action_txn_clear_manual()
        self.assertFalse(july.txn_manual)
        self.st.action_match_templates()
        self.assertTrue(july.txn_proposal_id)

    def test_edited_total_must_still_tie(self):
        self.p.line_ids[0].price_unit = 100.00
        self.assertTrue(self.p.block_reason.startswith(u'total '), self.p.block_reason)
        self.assertEqual(self.p.status_label, 'Blocked')

    def test_discard_then_rerun_rebuilds(self):
        self.p.line_ids[0].name = 'edited'
        self.p.action_discard()
        self.assertFalse(self.p.exists())
        self.assertFalse(self.st.line_ids.mapped('txn_proposal_id'))
        self.st.action_match_templates()
        new = self.st.txn_proposal_ids
        self.assertEqual(len(new), 1)
        self.assertFalse(new.edited)
        self.assertEqual(len(new.statement_line_ids), 2)

    def test_cannot_discard_approved(self):
        self.p.action_approve()
        with self.assertRaises(UserError):
            self.p.action_discard()


class TestRemoveFromSaleGroup(TxnCase):

    def test_remove_one_payment_from_a_group(self):
        self.make_template(
            name='Weekly', kind='sale_order', match_text='transfer from j citizen',
            amount_min=20.0, amount_max=50.0, account_id=False,
            partner_id=self.customer.id, product_id=self.service.id,
            tax_ids=[(6, 0, [self.sale_tax.id])])
        st = self._weekly_statement()
        st.action_match_templates()
        p = st.txn_proposal_ids
        st.line_ids.sorted('date')[-1].action_txn_set_manual()
        self.assertEqual([(l.quantity, l.price_unit) for l in p.line_ids], [(2.0, 40.0)])
        self.assertFalse(p.block_reason, p.block_reason)

    def _weekly_statement(self):
        return self.make_statement([(D(2026, 7, 2) + timedelta(weeks=i), 44.00, 'Transfer from J CITIZEN')
                                    for i in range(3)])
