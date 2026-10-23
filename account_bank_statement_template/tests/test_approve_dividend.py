# -*- coding: utf-8 -*-
from odoo.exceptions import ValidationError

from .common import TxnCase, D


class TestApproveDividend(TxnCase):

    def setUp(self):
        super(TestApproveDividend, self).setUp()
        Account = self.env['account.account']
        ref = self.env.ref
        revenue = ref('account.data_account_type_revenue').id
        self.franked = Account.create({'code': 'TXN821', 'name': 'Test Div Franked', 'user_type_id': revenue})
        self.unfranked = Account.create({'code': 'TXN820', 'name': 'Test Div Unfranked', 'user_type_id': revenue})
        self.fc_income = Account.create({'code': 'TXN823', 'name': 'Test FC Income', 'user_type_id': revenue})
        self.fc_credit = Account.create({
            'code': 'TXN331', 'name': 'Test FC Tax Credit',
            'user_type_id': ref('account.data_account_type_current_assets').id})
        self.div_t = self.make_template(
            name='ACME', kind='dividend', direction='in', match_text='acme dividend',
            amount_min=1.0, amount_max=5000.0, account_id=False, description='ACME 100 Div',
            franked_pct=100.0, company_tax_rate=30.0,
            div_franked_account_id=self.franked.id, div_unfranked_account_id=self.unfranked.id,
            franking_income_account_id=self.fc_income.id, franking_credit_account_id=self.fc_credit.id)

    def matched(self, amount, name='Direct Credit 123 ACME DIVIDEND XYZ'):
        st = self.make_statement([(D(2026, 9, 24), amount, name)])
        st.action_match_templates()
        return st, st.txn_proposal_ids

    def lines(self, p):
        return [(l.name, l.account_id, l.price_unit) for l in p.line_ids]

    def test_fully_franked_proposal(self):
        st, p = self.matched(70.00)
        self.assertEqual(p.kind, 'dividend')
        self.assertEqual(p.franked_amount, 70.00)
        self.assertEqual(self.lines(p), [
            ('ACME 100 Div - Received (franked)', self.franked, 70.00),
            ('ACME 100 Div - Received (unfranked)', self.unfranked, 0.00),
            ('ACME 100 Div - Franking Credit - Income', self.fc_income, 30.00),
            ('ACME 100 Div - Franking Credit - Tax Credit', self.fc_credit, -30.00)])
        self.assertFalse(p.block_reason)

    def test_approve_posts_credit_and_debit_sides(self):
        st, p = self.matched(70.00)
        p.action_approve()
        self.assertEqual(p.state, 'approved', p.fail_reason)
        move = st.line_ids.journal_entry_ids
        self.assertEqual(move.date, '2026-09-24')
        got = sorted((l.account_id.code, l.debit, l.credit) for l in move.line_ids)
        bank = st.journal_id.default_debit_account_id.code
        self.assertEqual(got, sorted([(bank, 70.0, 0.0), ('TXN331', 30.0, 0.0),
                                      ('TXN821', 0.0, 70.0), ('TXN823', 0.0, 30.0)]))

    def test_partly_franked_set_on_proposal(self):
        self.div_t.franked_pct = 0.0
        st, p = self.matched(100.00)
        self.assertEqual([l[2] for l in self.lines(p)], [0.0, 100.0, 0.0, 0.0])
        p.write({'franked_amount': 35.00})
        self.assertEqual([l[2] for l in self.lines(p)], [35.0, 65.0, 15.0, -15.0])
        self.assertTrue(p.edited)
        p.invalidate_cache()
        self.assertFalse(p.block_reason)
        p.action_approve()
        self.assertEqual(p.state, 'approved', p.fail_reason)
        credits = sorted((l.account_id.code, l.credit) for l in st.line_ids.journal_entry_ids.line_ids if l.credit)
        self.assertEqual(credits, [('TXN820', 65.0), ('TXN821', 35.0), ('TXN823', 15.0)])

    def test_franked_amount_keeps_edited_descriptions(self):
        st, p = self.matched(70.00)
        p.line_ids[0].name = 'ACME 120 Div - Received (franked)'
        p.write({'franked_amount': 50.00})
        self.assertEqual(p.line_ids[0].name, 'ACME 120 Div - Received (franked)')
        self.assertEqual(p.line_ids[0].price_unit, 50.00)

    def test_franked_above_bank_blocked(self):
        st, p = self.matched(70.00)
        p.write({'franked_amount': 80.00})
        p.invalidate_cache()
        self.assertEqual(p.block_reason, 'franked amount above bank amount')

    def test_dividend_template_checks_percentages(self):
        with self.assertRaises(ValidationError) as e:
            self.div_t.franked_pct = 120.0
        self.assertIn('franked % and tax rate', e.exception.name)

    def test_dividend_template_needs_accounts(self):
        with self.assertRaises(ValidationError):
            self.div_t.div_unfranked_account_id = False
