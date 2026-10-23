# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase, at_install, post_install


@at_install(False)
@post_install(True)
class TestInstall(TransactionCase):

    def test_module_installed(self):
        mod = self.env['ir.module.module'].search(
            [('name', '=', 'account_bank_statement_template')])
        self.assertEqual(mod.state, 'installed')
