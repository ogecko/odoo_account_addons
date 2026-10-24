# -*- coding: utf-8 -*-
from odoo.tests.common import at_install, post_install

from .common import TxnCase, D


@at_install(False)
@post_install(True)
class TestLastStatement(TxnCase):

    def setUp(self):
        super(TestLastStatement, self).setUp()
        # Only this test's templates, so real ones don't pick the journals.
        self.env['account.bank.txn.template'].search([]).write({'active': False})
        self.Statement = self.env['account.bank.statement']

    def test_opens_latest_statement_on_proposals_tab(self):
        self.make_template(journal_id=self.journal.id)
        self.make_statement([(D(2026, 8, 3), 5.0, 'x')])
        latest = self.make_statement([(D(2026, 9, 28), 5.0, 'x')])
        self.make_statement([(D(2026, 9, 1), 5.0, 'x')])
        action = self.Statement.action_open_last()
        self.assertEqual((action['res_model'], action['res_id']), ('account.bank.statement', latest.id))
        self.assertEqual(action['views'], [(False, 'form')])
        self.assertEqual(action['context'], {'open_tab': 'txn_proposals'})

    def test_same_date_takes_newest(self):
        self.make_template(journal_id=self.journal.id)
        self.make_statement([(D(2026, 9, 28), 5.0, 'x')])
        newer = self.make_statement([(D(2026, 9, 28), 6.0, 'y')])
        self.assertEqual(self.Statement.action_open_last()['res_id'], newer.id)

    def test_ignores_journals_without_templates(self):
        self.make_template(journal_id=self.journal.id)
        mine = self.make_statement([(D(2026, 9, 1), 5.0, 'x')])
        other = self.env['account.journal'].create({'name': 'Other Bank', 'code': 'TXNO', 'type': 'bank'})
        self.Statement.create({'journal_id': other.id, 'date': '2026-09-30'})
        self.assertEqual(self.Statement.action_open_last()['res_id'], mine.id)

    def test_template_without_journal_means_any_journal(self):
        self.make_template()
        other = self.env['account.journal'].create({'name': 'Other Bank', 'code': 'TXNO', 'type': 'bank'})
        latest = self.Statement.create({'journal_id': other.id, 'date': '2099-01-31'})
        self.assertEqual(self.Statement.action_open_last()['res_id'], latest.id)

    def test_no_statement_opens_list(self):
        empty = self.env['account.journal'].create({'name': 'Empty Bank', 'code': 'TXNE', 'type': 'bank'})
        self.make_template(journal_id=empty.id)
        action = self.Statement.action_open_last()
        self.assertEqual(action['res_model'], 'account.bank.statement')
        self.assertFalse(action.get('res_id'))

    def test_menu_after_templates(self):
        ref = self.env.ref
        menu = ref('account_bank_statement_template.menu_bank_statement_last')
        templates = ref('account_bank_statement_template.menu_bank_txn_template')
        self.assertEqual(menu.name, 'Bank Statement (last)')
        self.assertEqual(menu.parent_id, templates.parent_id)
        self.assertGreater(menu.sequence, templates.sequence)
        siblings = templates.parent_id.child_id.sorted(lambda m: (m.sequence, m.id))
        self.assertEqual(siblings[list(siblings).index(templates) + 1], menu)

    def test_tab_name_matches_page(self):
        arch = self.Statement.fields_view_get(view_type='form')['arch']
        self.assertIn('name="txn_proposals"', arch)
