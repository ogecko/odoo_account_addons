# -*- coding: utf-8 -*-
from odoo import fields
from odoo.exceptions import UserError

from .common import TxnCase, D


class TestApproveDirect(TxnCase):

    def setUp(self):
        super(TestApproveDirect, self).setUp()
        self.interest_t = self.make_template(
            name='Interest', match_text='credit interest', amount_min=1.0, amount_max=500.0)

    def lock(self, date):
        # Direct write: the real database copy these tests run in has unposted
        # entries, and Odoo won't set a lock date over them through the ORM.
        company = self.env.user.company_id
        self.env.cr.execute('UPDATE res_company SET fiscalyear_lock_date = %s WHERE id = %s',
                            (date, company.id))
        company.invalidate_cache()

    def matched(self, lines):
        st = self.make_statement(lines)
        st.action_match_templates()
        return st, st.txn_proposal_ids.sorted('date')

    def test_interest_entry_posted_on_bank_date(self):
        st, p = self.matched([(D(2026, 7, 1), 12.34, 'Credit Interest')])
        p.action_approve()
        self.assertEqual(p.state, 'approved')
        move = st.line_ids.journal_entry_ids
        self.assertEqual(len(move), 1)
        self.assertEqual(move.date, '2026-07-01')
        self.assertEqual(move.state, 'posted')
        self.assertEqual(move.txn_proposal_id, p)
        self.assertEqual(p.move_ids, move)
        income = move.line_ids.filtered(lambda l: l.account_id == self.interest_income)
        self.assertAlmostEqual(income.credit, 12.34, places=2)
        self.assertAlmostEqual(sum(move.line_ids.mapped('debit')), 12.34, places=2)

    def test_approve_is_idempotent(self):
        st, p = self.matched([(D(2026, 7, 1), 12.34, 'Credit Interest')])
        p.action_approve()
        moves = self.env['account.move'].search_count([])
        p.action_approve()
        self.assertEqual(self.env['account.move'].search_count([]), moves)
        self.assertEqual(p.state, 'approved')

    def test_line_reconciled_by_hand_meanwhile_refused(self):
        st, p = self.matched([(D(2026, 7, 1), 12.34, 'Credit Interest')])
        st.line_ids.process_reconciliation(new_aml_dicts=[{
            'name': 'by hand', 'account_id': self.income.id, 'debit': 0.0, 'credit': 12.34}])
        p.invalidate_cache()
        with self.assertRaises(UserError) as e:
            p.action_approve()
        self.assertIn('bank line already reconciled', e.exception.name)
        self.assertFalse(self.env['account.move'].search([('txn_proposal_id', '=', p.id)]))
        self.assertEqual(p.state, 'open')

    def test_failure_rolls_back_only_that_proposal(self):
        st, ps = self.matched([(D(2026, 7, 1), 12.34, 'Credit Interest'),
                               (D(2026, 9, 1), 34.56, 'Credit Interest')])
        self.lock('2026-07-31')
        st.action_approve_ready()
        early, late = ps
        self.assertEqual(early.state, 'failed')
        self.assertEqual(late.state, 'approved')
        self.assertFalse(early.statement_line_ids.journal_entry_ids)
        self.assertTrue(late.statement_line_ids.journal_entry_ids)

    def test_failed_proposal_can_be_approved_again(self):
        st, p = self.matched([(D(2026, 7, 1), 12.34, 'Credit Interest')])
        self.lock('2026-07-01')
        p.action_approve()
        self.assertEqual(p.state, 'failed')
        self.assertFalse(p.block_reason)
        self.lock(None)
        p.action_approve()
        self.assertEqual(p.state, 'approved', p.fail_reason)
        self.assertFalse(p.fail_reason)

    def test_approve_all_retries_failed(self):
        st, p = self.matched([(D(2026, 7, 1), 12.34, 'Credit Interest')])
        self.lock('2026-07-01')
        st.action_approve_ready()
        self.assertEqual(p.state, 'failed')
        self.lock(None)
        st.action_approve_ready()
        self.assertEqual(p.state, 'approved', p.fail_reason)

    def test_lock_date_fails_cleanly(self):
        st, p = self.matched([(D(2026, 7, 1), 12.34, 'Credit Interest')])
        self.lock('2026-07-01')
        p.action_approve()
        self.assertEqual(p.state, 'failed')
        self.assertIn('lock date', p.fail_reason)
        self.assertFalse(st.line_ids.journal_entry_ids)
        self.assertFalse(self.env['account.move'].search([('txn_proposal_id', '=', p.id)]))

    def test_approve_uses_snapshot_not_template(self):
        st, p = self.matched([(D(2026, 7, 1), 12.34, 'Credit Interest')])
        self.interest_t.account_id = self.income
        p.action_approve()
        accounts = st.line_ids.journal_entry_ids.line_ids.mapped('account_id')
        self.assertIn(self.interest_income, accounts)
        self.assertNotIn(self.income, accounts)

    def test_approve_ready_skips_blocked(self):
        st, p = self.matched([(D(2026, 7, 1), 12.34, 'Credit Interest')])
        p.line_ids.price_unit = 12.00
        st.action_approve_ready()
        self.assertEqual(p.state, 'open')
        self.assertFalse(st.line_ids.journal_entry_ids)
