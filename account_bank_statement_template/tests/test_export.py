# -*- coding: utf-8 -*-
from odoo.tests.common import at_install, post_install

from .common import TxnCase


@at_install(False)
@post_install(True)
class TestExportImport(TxnCase):
    """The saved export list round-trips through Odoo's own export and import."""

    def setUp(self):
        super(TestExportImport, self).setUp()
        self.T = self.env['account.bank.txn.template']
        self.T.search([]).write({'active': False})
        export = self.env.ref('account_bank_statement_template.txn_template_export')
        self.fields = export.export_fields.mapped('name')
        ref = self.env.ref
        Account = self.env['account.account']
        acc = lambda code: Account.create({'code': code, 'name': 'Test %s' % code,
                                           'user_type_id': ref('account.data_account_type_revenue').id})
        self.franked, self.unfranked, self.fc_income = acc('TXN821'), acc('TXN820'), acc('TXN823')
        self.fc_credit = Account.create({'code': 'TXN331', 'name': 'Test FC Credit',
                                         'user_type_id': ref('account.data_account_type_current_assets').id})
        self.make_template(name='Interest', journal_id=self.journal.id, match_text='interest')
        self.make_template(name='Sale', kind='sale_order', journal_id=self.journal.id, match_text='sale',
                           account_id=False, partner_id=self.customer.id, product_id=self.service.id,
                           tax_ids=[(6, 0, [self.sale_tax.id])], description='Services {year}')
        self.make_template(name='Split', kind='split', direction='out', match_text='TAX.*99', match_is_regex=True,
                           journal_id=self.journal.id, account_id=False,
                           split_ids=[(0, 0, {'keyword': 'GST', 'account_id': self.gst.id, 'description': 'GST'}),
                                      (0, 0, {'keyword': 'PAYG', 'account_id': self.expense.id})])
        self.make_template(name='Div', kind='dividend', journal_id=self.journal.id, match_text='div',
                           account_id=False, franked_pct=30, description='ABC 100 Div',
                           div_franked_account_id=self.franked.id, div_unfranked_account_id=self.unfranked.id,
                           franking_income_account_id=self.fc_income.id,
                           franking_credit_account_id=self.fc_credit.id)

    def snapshot(self, templates):
        return [(t.name, t.kind, t.journal_id, t.direction, t.match_text, t.match_is_regex,
                 t.amount_min, t.amount_max, t.account_id, t.partner_id, t.product_id, t.tax_ids,
                 t.description, t.franked_pct, t.div_franked_account_id, t.div_unfranked_account_id,
                 t.franking_income_account_id, t.franking_credit_account_id,
                 [(s.keyword, s.account_id, s.description) for s in t.split_ids])
                for t in templates.sorted('name')]

    def test_export_list_covers_editable_fields(self):
        skip = {'company_id', 'split_ids'}
        editable = {n for n, f in self.T._fields.items()
                    if f.store and not f.readonly and not f.automatic and n not in skip
                    and n not in ('create_uid', 'create_date', 'write_uid', 'write_date')}
        listed = {f.split('/')[0] for f in self.fields}
        self.assertEqual(editable - listed, set())

    def test_round_trip(self):
        originals = self.T.search([])
        before = self.snapshot(originals)
        rows = originals.export_data(self.fields)['datas']
        self.assertIn(self.journal.display_name, [r[self.fields.index('journal_id')] for r in rows])
        originals.unlink()
        result = self.T.load(self.fields, rows)
        self.assertEqual(result['messages'], [])
        self.assertEqual(self.snapshot(self.T.browse(result['ids'])), before)
