# -*- coding: utf-8 -*-
from odoo.exceptions import UserError
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
        self.saved = export.export_fields.mapped('name')
        # What Odoo's Export screen sends with "Import-Compatible Export": an id
        # column, and links as external ids (web/controllers/main.py get_fields).
        self.fields = ['id'] + [f + '/id' if not f.endswith('/id') and self.T._field_at(f).type
                                in ('many2one', 'many2many') else f for f in self.saved]
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
        listed = {f.split('/')[0] for f in self.saved}
        self.assertEqual(editable - listed, set())

    def test_round_trip_to_another_database(self):
        originals = self.T.search([])
        before = self.snapshot(originals)
        rows = originals.export_data(self.fields)['datas']
        # Links are names (or real external ids), never ids made up for the export.
        linked = [self.journal, self.customer, self.service, self.sale_tax, self.gst, self.franked]
        Data = self.env['ir.model.data']
        made_up = [r for r in linked if Data.search([('module', '=', '__export__'), ('model', '=', r._name),
                                                    ('res_id', '=', r.id)])]
        self.assertFalse(made_up)
        values = [v for r in rows for v in r[1:]]
        self.assertFalse([v for v in values if '__export__' in (v if isinstance(v, basestring) else '')])
        self.assertIn(self.journal.name, [r[self.fields.index('journal_id/id')] for r in rows])
        self.assertIn('TXN821', [r[self.fields.index('div_franked_account_id/id')] for r in rows])
        self.assertIn(self.customer.display_name, [r[self.fields.index('partner_id/id')] for r in rows])
        # Another database has none of the template ids.
        originals.unlink()
        result = self.T.load(self.fields, rows)
        self.assertEqual(result['messages'], [])
        self.assertEqual(self.snapshot(self.T.browse(result['ids'])), before)

    def test_round_trip_display_names(self):
        """Without "Import-Compatible Export", links are display names."""
        originals = self.T.search([])
        before = self.snapshot(originals)
        rows = originals.export_data(self.saved)['datas']
        self.assertIn(self.journal.display_name, [r[self.saved.index('journal_id')] for r in rows])
        originals.unlink()
        result = self.T.load(self.saved, rows)
        self.assertEqual(result['messages'], [])
        self.assertEqual(self.snapshot(self.T.browse(result['ids'])), before)

    def test_unknown_name_is_reported(self):
        rows = self.T.search([('name', '=', 'Sale')]).export_data(self.fields)['datas']
        rows[0][self.fields.index('partner_id/id')] = 'Nobody Pty Ltd'
        with self.assertRaises(UserError) as e:
            self.T.load(self.fields, rows)
        self.assertEqual(e.exception.name, "Row 2: no Partner named 'Nobody Pty Ltd'.")

    def counts(self):
        return (self.T.search_count([]), self.env['account.bank.txn.template.split'].search_count([]))

    def test_list_leaves_ids_to_the_export_screen(self):
        self.assertNotIn('id', self.saved)
        self.assertNotIn('split_ids/id', self.saved)

    def test_split_line_ids_are_ignored(self):
        """A file with split line ids from another database still loads."""
        templates = self.T.search([])
        fields = self.fields + ['split_ids/id']
        rows = templates.export_data(fields)['datas']
        rows = [r[:-1] + ['__export__.elsewhere_%d' % n if r[-1] else ''] for n, r in enumerate(rows)]
        before, counts = self.snapshot(templates), self.counts()
        self.assertEqual(self.T.load(fields, rows)['messages'], [])
        self.assertEqual(self.counts(), counts)
        templates.invalidate_cache()
        self.assertEqual(self.snapshot(templates), before)

    def test_reimport_updates_in_place(self):
        templates = self.T.search([])
        before, counts = self.snapshot(templates), self.counts()
        rows = templates.export_data(self.fields)['datas']
        result = self.T.load(self.fields, rows)
        self.assertEqual(result['messages'], [])
        self.assertEqual(sorted(result['ids']), sorted(templates.ids))
        self.assertEqual(self.counts(), counts)
        templates.invalidate_cache()
        self.assertEqual(self.snapshot(templates), before)
        # An edit in the file lands on the existing template.
        col = self.fields.index('match_text')
        for row in rows:
            if row[col] == 'interest':
                row[col] = 'interest paid'
        self.T.load(self.fields, rows)
        self.assertEqual(self.counts(), counts)
        self.assertEqual(self.T.search([('name', '=', 'Interest')]).match_text, 'interest paid')

    def test_blank_id_creates(self):
        templates = self.T.search([])
        rows = templates.export_data(self.fields)['datas']
        rows = [[''] + r[1:] for r in rows]
        count = self.counts()[0]
        self.assertEqual(self.T.load(self.fields, rows)['messages'], [])
        self.assertEqual(self.counts()[0], count * 2)
