# -*- coding: utf-8 -*-
from odoo.tests.common import at_install, post_install

from .common import TxnCase


@at_install(False)
@post_install(True)
class TestStatementImportBalances(TxnCase):
    """Banks give the balance as at the download day, which can be after the
    statement's last line; the import carries on from the previous statement."""

    def setUp(self):
        super(TestStatementImportBalances, self).setUp()
        self.Import = self.env['account.bank.statement.import']
        self.Statement = self.env['account.bank.statement']

    def statement(self, start, end, lines):
        return self.Statement.create({
            'journal_id': self.journal.id, 'balance_start': start, 'balance_end_real': end,
            'line_ids': [(0, 0, {'date': d, 'amount': a, 'name': 'x'}) for d, a in lines]})

    def imported(self, start, end, lines, uid='u'):
        vals = {'journal_id': self.journal.id, 'name': 'TXN-IMPORT', 'date': '2026-10-24',
                'balance_start': start, 'balance_end_real': end,
                'transactions': [{'date': d, 'amount': a, 'name': 'y',
                                  'unique_import_id': '%s-%d' % (uid, n)}
                                 for n, (d, a) in enumerate(lines)]}
        ids, notes = self.Import._create_bank_statements([vals])
        return self.Statement.browse(ids), notes

    def test_follows_previous_statement(self):
        self.statement(100.0, 150.0, [('2026-06-10', 50.0)])
        # File balance 230.00 is at the download day: 30.00 came in after 25 Sep.
        st, notes = self.imported(170.0, 230.0, [('2026-07-01', 40.0), ('2026-09-25', 20.0)])
        self.assertEqual((st.balance_start, st.balance_end_real, st.txn_bank_balance), (150.0, 210.0, 230.0))
        self.assertEqual(st.balance_end, st.balance_end_real)
        self.assertIn('Starting balance 150.00 taken from statement', notes[-1]['message'])

    def test_previous_is_by_line_dates_not_creation(self):
        older = self.statement(0.0, 10.0, [('2026-03-27', 10.0)])
        self.statement(10.0, 60.0, [('2026-06-25', 50.0)])
        st, _ = self.imported(0.0, 5.0, [('2026-07-01', 5.0)])
        self.assertEqual(st.balance_start, 60.0)
        self.assertEqual(st._txn_previous_statement().balance_end_real, 60.0)
        self.assertNotEqual(st._txn_previous_statement(), older)

    def test_first_statement_keeps_file_balances(self):
        st, notes = self.imported(100.0, 120.0, [('2026-07-01', 20.0)])
        self.assertEqual((st.balance_start, st.balance_end_real, st.txn_bank_balance), (100.0, 120.0, 120.0))

    def test_no_note_when_file_agrees(self):
        self.statement(100.0, 150.0, [('2026-06-10', 50.0)])
        st, notes = self.imported(150.0, 170.0, [('2026-07-01', 20.0)])
        self.assertEqual(st.balance_end_real, 170.0)
        self.assertFalse([n for n in notes if 'Starting balance' in n['message']])

    def test_other_journal_ignored(self):
        other = self.env['account.journal'].create({'name': 'Other Bank', 'code': 'TXNO', 'type': 'bank'})
        self.Statement.create({'journal_id': other.id, 'balance_start': 0.0, 'balance_end_real': 999.0,
                               'line_ids': [(0, 0, {'date': '2026-06-10', 'amount': 999.0, 'name': 'x'})]})
        st, _ = self.imported(100.0, 120.0, [('2026-07-01', 20.0)])
        self.assertEqual(st.balance_start, 100.0)
