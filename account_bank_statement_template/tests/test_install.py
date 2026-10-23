# -*- coding: utf-8 -*-
from lxml import etree

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


@at_install(False)
@post_install(True)
class TestScreenLayout(TransactionCase):
    """Owner feedback from the 2026Q2 trial."""

    def test_buttons_on_proposals_tab_not_header(self):
        arch = etree.fromstring(self.env['account.bank.statement'].fields_view_get(view_type='form')['arch'])
        for name, label in (('action_match_templates', 'Generate'), ('action_approve_ready', 'Approve All')):
            self.assertFalse(arch.xpath('//header//button[@name="%s"]' % name), name)
            tab = arch.xpath('//page[@name="txn_proposals"]//button[@name="%s"]' % name)
            self.assertEqual(len(tab), 1, name)
            self.assertEqual(tab[0].get('string'), label)

    def test_lines_matched_title_with_count(self):
        arch = self.env['account.bank.statement'].fields_view_get(view_type='form')['arch']
        self.assertIn('Lines Matched', arch)
        self.assertIn('txn_proposal_count', arch)
        self.assertIn('proposals generated', arch)

    def test_template_form_lists_description_placeholders(self):
        arch = self.env['account.bank.txn.template'].fields_view_get(view_type='form')['arch']
        for placeholder in ('{year}', '{first_week}', '{last_week}', '{quarter}', '{date}'):
            self.assertIn(placeholder, arch)
