# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo.exceptions import UserError

from .common import TxnCase, D


class TestApproveSale(TxnCase):

    def setUp(self):
        super(TestApproveSale, self).setUp()
        self.weekly_t = self.make_template(
            name='Weekly', kind='sale_order', match_text='transfer from j citizen',
            amount_min=20.0, amount_max=50.0, account_id=False,
            partner_id=self.customer.id, product_id=self.service.id,
            tax_ids=[(6, 0, [self.sale_tax.id])],
            description='Weekly Service {year} (Wk{first_week} to Wk{last_week})')

    def approved(self, start=D(2026, 4, 2)):
        lines = [(start, 33.00, 'Transfer from J CITIZEN NetBank')]
        lines += [(start + timedelta(weeks=1 + i), 44.00, 'Transfer from J CITIZEN NetBank')
                  for i in range(12)]
        st = self.make_statement(lines)
        st.action_match_templates()
        p = st.txn_proposal_ids
        p.action_approve()
        return st, p

    def test_sale_mixed_prices_invoice_ties(self):
        st, p = self.approved()
        self.assertEqual(p.state, 'approved', p.fail_reason)
        inv = p.invoice_id
        self.assertEqual(inv.state, 'paid')
        self.assertAlmostEqual(inv.amount_total, 561.00, places=2)
        self.assertEqual([(l.quantity, l.price_unit) for l in inv.invoice_line_ids.sorted('price_unit')],
                         [(1.0, 30.0), (12.0, 40.0)])
        self.assertEqual(len(st.line_ids.mapped('journal_entry_ids')), 13)

    def test_invoice_date_is_proposal_date_not_today(self):
        st, p = self.approved(start=D(2026, 4, 2))  # last line 25 Jun, approved in a later quarter
        self.assertEqual(p.date, '2026-06-25')
        inv = p.invoice_id
        self.assertEqual(inv.date_invoice, '2026-06-25')
        self.assertEqual(inv.date, '2026-06-25')
        self.assertEqual(inv.move_id.date, '2026-06-25')
        self.assertEqual(p.sale_order_id.date_order[:10], '2026-06-25')

    def test_order_confirmed_on_invoice_date(self):
        st, p = self.approved(start=D(2026, 4, 2))
        self.assertEqual(p.invoice_date, '2026-06-25')
        self.assertEqual(p.sale_order_id.confirmation_date[:10], '2026-06-25')

    def test_invoice_date_override(self):
        lines = [(D(2026, 4, 2) + timedelta(weeks=i), 44.00, 'Transfer from J CITIZEN NetBank')
                 for i in range(13)]
        st = self.make_statement(lines)
        st.action_match_templates()
        p = st.txn_proposal_ids
        p.invoice_date = '2026-06-30'  # after the statement's last line, same quarter
        self.assertFalse(p.block_reason)
        p.action_approve()
        self.assertEqual(p.state, 'approved', p.fail_reason)
        inv = p.invoice_id
        self.assertEqual((inv.date_invoice, inv.date, inv.move_id.date), ('2026-06-30',) * 3)
        self.assertEqual(p.sale_order_id.date_order[:10], '2026-06-30')
        self.assertEqual(p.sale_order_id.confirmation_date[:10], '2026-06-30')
        # The payments stay on their own bank dates.
        for st_line in p.statement_line_ids:
            self.assertEqual(st_line.journal_entry_ids.date, st_line.date)

    def test_no_delivery_for_service(self):
        st, p = self.approved()
        self.assertEqual(p.sale_order_id.state, 'sale')
        self.assertFalse(p.sale_order_id.picking_ids)

    def test_order_and_invoice_link_back(self):
        st, p = self.approved()
        self.assertEqual(p.sale_order_id.txn_proposal_id, p)
        self.assertEqual(p.invoice_id.txn_proposal_id, p)
        self.assertEqual(p.invoice_id.move_id.txn_proposal_id, p)
        self.assertEqual(p.sale_order_id.partner_id, self.customer)

    def test_blocked_proposal_not_approved(self):
        st = self.make_statement([(D(2026, 7, 2), 44.00, 'Transfer from J CITIZEN NetBank')])
        st.action_match_templates()
        p = st.txn_proposal_ids
        p.line_ids.price_unit = 40.01
        with self.assertRaises(UserError):
            p.action_approve()
        self.assertFalse(self.env['sale.order'].search([('txn_proposal_id', '=', p.id)]))
        self.assertEqual(p.state, 'open')


class TestApproveSaleDeliveredService(TestApproveSale):
    """A service invoiced on delivered quantities, tracked by hand (as the
    real weekly-services product is): approval states the services delivered."""

    def setUp(self):
        super(TestApproveSaleDeliveredService, self).setUp()
        self.service.product_tmpl_id.write({'invoice_policy': 'delivery', 'track_service': 'manual'})

    def test_services_marked_delivered_and_invoiced(self):
        st, p = self.approved()
        self.assertEqual(p.state, 'approved', p.fail_reason)
        for line in p.sale_order_id.order_line:
            self.assertEqual(line.qty_delivered, line.product_uom_qty)
        self.assertEqual(p.invoice_id.state, 'paid')
