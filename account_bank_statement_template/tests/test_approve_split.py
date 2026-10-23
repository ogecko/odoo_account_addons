# -*- coding: utf-8 -*-
from odoo.exceptions import ValidationError

from .common import TxnCase, D

BAS = 'TAX OFFICE PAYMENTS NetBank BPAY 75556 1111222233334444 '


class TestApproveSplit(TxnCase):

    def setUp(self):
        super(TestApproveSplit, self).setUp()
        Account = self.env['account.account']
        liability = self.env.ref('account.data_account_type_current_liabilities').id
        self.gst_net = Account.create({'code': 'TXN355', 'name': 'Test GST Net', 'user_type_id': liability})
        self.payg = Account.create({'code': 'TXN333', 'name': 'Test PAYG', 'user_type_id': liability})
        self.bas_t = self.make_template(
            name='BAS', kind='split', direction='out', match_text='tax office payments.*4444',
            match_is_regex=True, amount_min=1.0, amount_max=5000.0, account_id=False,
            split_ids=[(0, 0, {'keyword': 'GST', 'account_id': self.gst_net.id,
                               'description': 'GST period ending {period_end}'}),
                       (0, 0, {'keyword': 'PAYG', 'account_id': self.payg.id,
                               'description': 'PAYG period ending {period_end}'})])

    def matched(self, lines):
        st = self.make_statement(lines)
        st.action_match_templates()
        return st, st.txn_proposal_ids

    def test_split_from_description(self):
        st, p = self.matched([(D(2026, 1, 5), -312.00, BAS + 'GST 12 PAYG 300'),
                              (D(2026, 1, 9), -100.00, 'TAX OFFICE PAYMENTS NetBank BPAY 75556 9999 Income Tax')])
        self.assertEqual(len(p), 1)
        self.assertEqual(p.kind, 'split')
        self.assertEqual([(l.name, l.account_id, l.price_unit) for l in p.line_ids],
                         [('GST period ending 31 Dec 2025', self.gst_net, 12.0),
                          ('PAYG period ending 31 Dec 2025', self.payg, 300.0)])
        self.assertFalse(p.block_reason)
        self.assertFalse(p.invoice_date)

    def test_approve_posts_one_entry_on_bank_date(self):
        st, p = self.matched([(D(2026, 1, 5), -312.00, BAS + 'GST 12 PAYG 300')])
        p.action_approve()
        self.assertEqual(p.state, 'approved', p.fail_reason)
        move = st.line_ids.journal_entry_ids
        self.assertEqual(len(move), 1)
        self.assertEqual(move.date, '2026-01-05')
        debits = sorted((l.account_id.code, l.debit) for l in move.line_ids if l.debit)
        self.assertEqual(debits, [('TXN333', 300.0), ('TXN355', 12.0)])

    def test_missing_keyword_is_zero_and_not_posted(self):
        st, p = self.matched([(D(2026, 4, 28), -37.00, BAS + 'GST37')])
        self.assertEqual([l.price_unit for l in p.line_ids], [37.0, 0.0])
        p.action_approve()
        self.assertEqual(p.state, 'approved', p.fail_reason)
        move = st.line_ids.journal_entry_ids
        self.assertEqual(len(move.line_ids), 2)
        self.assertEqual(move.line_ids.filtered('debit').account_id, self.gst_net)

    def test_no_amounts_in_description_set_by_hand(self):
        st, p = self.matched([(D(2026, 4, 28), -50.00, BAS + 'BAS')])
        self.assertEqual([l.price_unit for l in p.line_ids], [0.0, 0.0])
        self.assertTrue(p.block_reason.startswith(u'total 0.00'), p.block_reason)
        p.line_ids[0].price_unit = 20.0
        p.line_ids[1].price_unit = 30.0
        p.invalidate_cache()  # as the next request from the dialog would
        self.assertFalse(p.block_reason)
        p.action_approve()
        self.assertEqual(p.state, 'approved', p.fail_reason)

    def test_split_template_needs_parts(self):
        with self.assertRaises(ValidationError):
            self.make_template(name='Empty', kind='split', account_id=False)
