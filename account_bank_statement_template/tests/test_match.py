# -*- coding: utf-8 -*-
from datetime import timedelta

from .common import TxnCase, D


class TestMatch(TxnCase):

    def setUp(self):
        super(TestMatch, self).setUp()
        self.interest_t = self.make_template(
            name='Interest', match_text='credit interest', amount_min=1.0, amount_max=500.0)
        self.weekly_t = self.make_template(
            name='Weekly', kind='sale_order', match_text='transfer from j citizen',
            amount_min=20.0, amount_max=50.0, account_id=False,
            partner_id=self.customer.id, product_id=self.service.id,
            tax_ids=[(6, 0, [self.sale_tax.id])],
            description='Weekly Service {year} (Wk{first_week} to Wk{last_week})')
        self.aws_t = self.make_template(
            name='AWS', kind='purchase_order', direction='out', match_text='amazon web services',
            amount_min=10.0, amount_max=900.0, account_id=False,
            partner_id=self.vendor.id, product_id=self.consumable.id,
            tax_ids=[(6, 0, [self.purch_tax_inc.id])], description='Web Services')
        self.mail_t = self.make_template(
            name='Mailgun', kind='purchase_order', direction='out', match_text='sinch mailgun',
            amount_min=0.5, amount_max=100.0, account_id=False,
            partner_id=self.vendor.id, product_id=self.service.id, description='Mail Services',
            companion_text='international transaction fee', companion_product_id=self.consumable.id,
            companion_description='FX fee')

    def proposals(self, st, template):
        return st.txn_proposal_ids.filtered(lambda p: p.template_id == template)

    def test_direct_one_proposal_per_line(self):
        st = self.make_statement([(D(2026, 7, 1), 12.34, 'Credit Interest'),
                                  (D(2026, 8, 1), 23.45, 'Credit Interest'),
                                  (D(2026, 9, 1), 34.56, 'Credit Interest')])
        st.action_match_templates()
        ps = self.proposals(st, self.interest_t)
        self.assertEqual(len(ps), 3)
        for p in ps:
            self.assertEqual(p.kind, 'direct')
            self.assertEqual(len(p.statement_line_ids), 1)
            self.assertEqual(p.date, p.statement_line_ids.date)
            self.assertEqual(p.line_ids.account_id, self.interest_income)
            self.assertAlmostEqual(p.line_ids.price_unit, p.statement_line_ids.amount, places=2)
            self.assertFalse(p.block_reason)

    def test_sale_groups_by_amount(self):
        lines = [(D(2026, 7, 2), 33.00, 'Transfer from J CITIZEN NetBank')]
        lines += [(D(2026, 7, 9) + timedelta(weeks=i), 44.00, 'Transfer from J CITIZEN NetBank')
                  for i in range(12)]
        st = self.make_statement(lines)
        st.action_match_templates()
        p = self.proposals(st, self.weekly_t)
        self.assertEqual(len(p), 1)
        self.assertEqual(p.partner_id, self.customer)
        self.assertEqual([(l.quantity, l.price_unit, l.name) for l in p.line_ids],
                         [(1.0, 30.0, 'Weekly Service 2026 (Wk27 to Wk27)'),
                          (12.0, 40.0, 'Weekly Service 2026 (Wk28 to Wk39)')])
        self.assertEqual(len(p.statement_line_ids), 13)
        self.assertFalse(p.block_reason, p.block_reason)

    def test_purchase_line_per_bank_line(self):
        st = self.make_statement([(D(2026, 7, 6), -111.11, 'AMAZON WEB SERVICES'),
                                  (D(2026, 8, 4), -222.22, 'AMAZON WEB SERVICES'),
                                  (D(2026, 9, 4), -333.33, 'AMAZON WEB SERVICES')])
        st.action_match_templates()
        p = self.proposals(st, self.aws_t)
        self.assertEqual([l.price_unit for l in p.line_ids], [111.11, 222.22, 333.33])
        self.assertEqual(p.line_ids.mapped('tax_ids'), self.purch_tax_inc)
        self.assertAlmostEqual(p.amount_total, 666.66, places=2)
        self.assertFalse(p.block_reason, p.block_reason)

    def test_purchase_companion_follows_charge(self):
        st = self.make_statement([
            (D(2026, 7, 2), -0.15, 'International Transaction Fee Value Date: 01/07/2026'),
            (D(2026, 7, 2), -5.00, 'SINCH MAILGUN SAN ANTONIO TX USA Card xx1 USD 3.30 Value Date: 02/07/2026'),
        ])
        st.action_match_templates()
        p = self.proposals(st, self.mail_t)
        self.assertEqual([(l.name, l.price_unit, l.product_id) for l in p.line_ids],
                         [('Mail Services', 5.00, self.service), ('FX fee', 0.15, self.consumable)])
        self.assertEqual(len(p.statement_line_ids), 2)
        self.assertFalse(p.block_reason, p.block_reason)

    def test_proposal_date_is_last_bank_line(self):
        st = self.make_statement([(D(2026, 7, 6), -111.11, 'AMAZON WEB SERVICES'),
                                  (D(2026, 9, 4), -333.33, 'AMAZON WEB SERVICES')])
        st.action_match_templates()
        self.assertEqual(self.proposals(st, self.aws_t).date, '2026-09-04')

    def test_unmatched_reasons_recorded(self):
        st = self.make_statement([(D(2026, 7, 1), 950.0, 'Credit Interest'),
                                  (D(2026, 7, 2), 5.0, 'Something else')])
        st.action_match_templates()
        out, other = st.line_ids.sorted('date')
        self.assertEqual(out.txn_manual_reason, 'out of range: Interest')
        self.assertEqual(other.txn_manual_reason, 'no match')
        self.assertFalse(st.txn_proposal_ids)

    def test_rerun_no_duplicates(self):
        st = self.make_statement([(D(2026, 7, 1), 12.34, 'Credit Interest'),
                                  (D(2026, 7, 6), -111.11, 'AMAZON WEB SERVICES')])
        st.action_match_templates()
        st.action_match_templates()
        self.assertEqual(len(st.txn_proposal_ids), 2)
        for line in st.line_ids:
            self.assertEqual(len(line.txn_proposal_id), 1)

    def test_rerun_keeps_edited_proposal(self):
        st = self.make_statement([(D(2026, 7, 6), -111.11, 'AMAZON WEB SERVICES')])
        st.action_match_templates()
        p = self.proposals(st, self.aws_t)
        p.line_ids.name = 'Edited by owner'
        p.edited = True
        st.action_match_templates()
        self.assertEqual(self.proposals(st, self.aws_t), p)
        self.assertEqual(p.line_ids.name, 'Edited by owner')

    def test_rerun_skips_manual_and_reconciled(self):
        st = self.make_statement([(D(2026, 7, 1), 12.34, 'Credit Interest'),
                                  (D(2026, 8, 1), 23.45, 'Credit Interest')])
        manual, by_hand = st.line_ids.sorted('date')
        manual.txn_manual = True
        by_hand.process_reconciliation(new_aml_dicts=[{
            'name': 'by hand', 'account_id': self.interest_income.id, 'debit': 0.0, 'credit': 23.45}])
        st.action_match_templates()
        self.assertFalse(st.txn_proposal_ids)

    def test_rerun_rebuilds_failed_proposal(self):
        st = self.make_statement([(D(2026, 7, 1), 12.34, 'Credit Interest')])
        st.action_match_templates()
        old = st.txn_proposal_ids
        old.write({'state': 'failed', 'fail_reason': 'test'})
        st.action_match_templates()
        self.assertFalse(old.exists())
        self.assertEqual(len(st.txn_proposal_ids), 1)
        self.assertEqual(st.txn_proposal_ids.state, 'open')

    def test_template_partner_is_used_not_created(self):
        before = self.env['res.partner'].search_count([])
        st = self.make_statement([(D(2026, 7, 6), -111.11, 'AMAZON WEB SERVICES')])
        st.action_match_templates()
        self.assertEqual(self.env['res.partner'].search_count([]), before)
        self.assertEqual(self.proposals(st, self.aws_t).partner_id, self.vendor)

    def test_proposal_count_on_statement(self):
        st = self.make_statement([(D(2026, 7, 1), 12.34, 'Credit Interest'),
                                  (D(2026, 7, 6), -111.11, 'AMAZON WEB SERVICES'),
                                  (D(2026, 7, 7), 5.00, 'Something else')])
        self.assertEqual(st.txn_proposal_count, 0)
        st.action_match_templates()
        self.assertEqual(st.txn_proposal_count, 2)
