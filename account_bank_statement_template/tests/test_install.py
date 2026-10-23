# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase, at_install, post_install


@at_install(False)
@post_install(True)
class TestInstall(TransactionCase):

    def test_module_installed(self):
        mod = self.env['ir.module.module'].search(
            [('name', '=', 'account_bank_statement_template')])
        self.assertEqual(mod.state, 'installed')


@at_install(False)
@post_install(True)
class TestViews(TransactionCase):

    def test_statement_form_has_matching_and_proposals(self):
        arch = self.env['account.bank.statement'].fields_view_get(view_type='form')['arch']
        for name in ('action_match_templates', 'action_approve_ready', 'txn_proposal_ids',
                     'txn_manual_line_ids'):
            self.assertIn(name, arch)

    def test_proposal_form_lines_editable(self):
        view = self.env['account.bank.txn.proposal'].fields_view_get(view_type='form')
        for name in ('line_ids', 'statement_line_ids', 'action_approve', 'action_discard', 'block_reason'):
            self.assertIn(name, view['arch'])
        # Odoo moves an embedded list's own view out of the form arch.
        self.assertIn('editable="bottom"', view['fields']['line_ids']['views']['tree']['arch'])
