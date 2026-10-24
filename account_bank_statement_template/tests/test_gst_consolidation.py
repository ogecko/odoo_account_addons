# -*- coding: utf-8 -*-
from odoo.exceptions import UserError
from odoo.tests.common import at_install, post_install

from .common import TxnCase


@at_install(False)
@post_install(True)
class TestGstConsolidation(TxnCase):

    def setUp(self):
        super(TestGstConsolidation, self).setUp()
        ref = self.env.ref
        Account = self.env['account.account']
        liability = ref('account.data_account_type_current_liabilities').id
        self.collected = Account.create({'code': 'TXN350', 'name': 'Test GST Collected', 'user_type_id': liability})
        self.paid = Account.create({'code': 'TXN345', 'name': 'Test GST Paid', 'user_type_id': liability})
        self.net = Account.create({'code': 'TXN355', 'name': 'Test GST Net', 'user_type_id': liability})
        self.misc = self.env['account.journal'].create({'name': 'Test Misc', 'code': 'TXNM', 'type': 'general'})
        # Made-up quarter: 79.74 collected, 40.04 paid by 30 Sep; October is next quarter.
        self.post('2026-08-01', self.collected, -79.74)
        self.post('2026-09-15', self.paid, 40.04)
        self.post('2026-10-02', self.collected, -11.00)

    def post(self, date, account, amount):
        move = self.env['account.move'].create({
            'journal_id': self.misc.id, 'date': date,
            'line_ids': [(0, 0, {'name': 'x', 'account_id': account.id,
                                 'debit': max(amount, 0), 'credit': max(-amount, 0)}),
                         (0, 0, {'name': 'x', 'account_id': self.income.id,
                                 'debit': max(-amount, 0), 'credit': max(amount, 0)})]})
        move.post()
        return move

    def wizard(self, **vals):
        values = {'period_end': '2026-09-30', 'journal_id': self.misc.id,
                  'collected_account_id': self.collected.id, 'paid_account_id': self.paid.id,
                  'net_account_id': self.net.id}
        values.update(vals)
        return self.env['account.gst.consolidation'].create(values)

    def balance(self, account):
        lines = self.env['account.move.line'].search([('account_id', '=', account.id), ('move_id.state', '=', 'posted')])
        return round(sum(lines.mapped('debit')) - sum(lines.mapped('credit')), 2)

    def test_defaults_from_quarter(self):
        w = self.wizard()
        self.assertEqual((w.name, w.date), ('2026 Q3 GST', '2026-09-30'))
        self.assertEqual((w.collected_balance, w.paid_balance), (79.74, 40.04))
        self.assertEqual((w.collected_amount, w.paid_amount, w.net_amount), (79.0, 40.0, 39.0))

    def test_default_quarter_is_the_last_complete_one(self):
        w = self.env['account.gst.consolidation'].with_context(tz='Australia/Sydney').new({})
        self.assertTrue(w._default_period_end()[5:] in ('03-31', '06-30', '09-30', '12-31'))

    def test_posts_one_entry_into_net(self):
        action = self.wizard().action_post()
        move = self.env['account.move'].browse(action['res_id'])
        self.assertEqual((move.state, move.ref, move.date, move.journal_id), ('posted', '2026 Q3 GST', '2026-09-30', self.misc))
        self.assertEqual(len(move.line_ids), 4)
        self.assertEqual(self.balance(self.net), -39.0)
        self.assertEqual(self.balance(self.collected), -11.74)  # 0.74 cents left + October
        self.assertEqual(self.balance(self.paid), 0.04)

    def test_amounts_can_be_changed_to_match_the_bas(self):
        move = self.env['account.move'].browse(self.wizard(collected_amount=80.0, paid_amount=40.0).action_post()['res_id'])
        self.assertEqual(self.balance(self.net), -40.0)
        self.assertEqual(sum(move.line_ids.mapped('debit')), 120.0)

    def test_second_time_refused(self):
        self.wizard().action_post()
        w = self.wizard()
        self.assertTrue(w.existing_move_ids)
        with self.assertRaises(UserError):
            w.action_post()

    def test_old_manual_adjustment_counts_as_done(self):
        """Manual Tax Adjustments left the label on the lines only."""
        move = self.env['account.move'].create({
            'journal_id': self.misc.id, 'date': '2026-09-30',
            'line_ids': [(0, 0, {'name': '2026 Q3 GST', 'account_id': self.collected.id, 'debit': 79.0}),
                         (0, 0, {'name': '2026 Q3 GST', 'account_id': self.net.id, 'credit': 79.0})]})
        move.post()
        w = self.wizard()
        self.assertEqual(w.existing_move_ids, move)
        with self.assertRaises(UserError):
            w.action_post()

    def test_zero_side_left_out(self):
        move = self.env['account.move'].browse(self.wizard(paid_amount=0.0).action_post()['res_id'])
        self.assertEqual(len(move.line_ids), 2)

    def test_nothing_to_move(self):
        with self.assertRaises(UserError):
            self.wizard(collected_amount=0.0, paid_amount=0.0).action_post()

    def test_menu_after_bank_statement_last(self):
        menu = self.env.ref('account_bank_statement_template.menu_gst_consolidation')
        last = self.env.ref('account_bank_statement_template.menu_bank_statement_last')
        self.assertEqual(menu.parent_id, last.parent_id)
        siblings = last.parent_id.child_id.sorted(lambda m: (m.sequence, m.id))
        self.assertEqual(siblings[list(siblings).index(last) + 1], menu)
