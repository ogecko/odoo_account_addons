# -*- coding: utf-8 -*-
from .common import TxnCase, D


class TestApprovePurchase(TxnCase):

    def setUp(self):
        super(TestApprovePurchase, self).setUp()
        self.aws_t = self.make_template(
            name='AWS', kind='purchase_order', direction='out', match_text='amazon web services',
            amount_min=10.0, amount_max=900.0, account_id=False,
            partner_id=self.vendor.id, product_id=self.consumable.id,
            tax_ids=[(6, 0, [self.purch_tax_inc.id])], description='Web Services')
        self.mail_t = self.make_template(
            name='Mailgun', kind='purchase_order', direction='out', match_text='sinch mailgun',
            amount_min=0.5, amount_max=100.0, account_id=False,
            partner_id=self.vendor.id, product_id=self.service.id, description='Mail Services',
            companion_text='international transaction fee', companion_product_id=self.service.id,
            companion_description='FX fee')

    def approve(self, lines, template):
        st = self.make_statement(lines)
        st.action_match_templates()
        p = st.txn_proposal_ids.filtered(lambda x: x.template_id == template)
        p.action_approve()
        return st, p

    def aws(self):
        return self.approve([(D(2026, 7, 6), -111.11, 'AMAZON WEB SERVICES'),
                             (D(2026, 8, 4), -222.22, 'AMAZON WEB SERVICES'),
                             (D(2026, 9, 4), -333.33, 'AMAZON WEB SERVICES')], self.aws_t)

    def test_purchase_included_tax_bill_ties(self):
        st, p = self.aws()
        self.assertEqual(p.state, 'approved', p.fail_reason)
        bill = p.invoice_id
        self.assertEqual(bill.type, 'in_invoice')
        self.assertEqual(bill.state, 'paid')
        self.assertAlmostEqual(bill.amount_total, 666.66, places=2)
        self.assertEqual(bill.invoice_line_ids.mapped('purchase_line_id.order_id'), p.purchase_order_id)
        self.assertEqual(len(st.line_ids.mapped('journal_entry_ids')), 3)

    def test_receipt_validated_for_consumable(self):
        st, p = self.aws()
        pickings = p.purchase_order_id.picking_ids
        self.assertTrue(pickings)
        self.assertEqual(set(pickings.mapped('state')), {'done'})

    def test_fee_lines_on_same_bill(self):
        st, p = self.approve([
            (D(2026, 7, 2), -0.15, 'International Transaction Fee Value Date: 01/07/2026'),
            (D(2026, 7, 2), -5.00, 'SINCH MAILGUN SAN ANTONIO TX USA Card xx1 USD 3.30 Value Date: 02/07/2026'),
            (D(2026, 8, 3), -0.18, 'International Transaction Fee Value Date: 01/08/2026'),
            (D(2026, 8, 3), -6.00, 'SINCH MAILGUN SAN ANTONIO TX USA Card xx1 USD 3.90 Value Date: 02/08/2026'),
        ], self.mail_t)
        self.assertEqual(p.state, 'approved', p.fail_reason)
        self.assertEqual(len(p.invoice_id.invoice_line_ids), 4)
        self.assertAlmostEqual(p.invoice_id.amount_total, 11.33, places=2)
        self.assertEqual(len(st.line_ids.mapped('journal_entry_ids')), 4)

    def test_bill_date_is_proposal_date(self):
        st, p = self.aws()
        self.assertEqual(p.date, '2026-09-04')
        self.assertEqual(p.invoice_id.date_invoice, '2026-09-04')
        self.assertEqual(p.invoice_id.move_id.date, '2026-09-04')
        self.assertEqual(p.purchase_order_id.date_order[:10], '2026-09-04')

    def test_no_receipt_for_service_product(self):
        self.aws_t.product_id = self.service
        st, p = self.aws()
        self.assertEqual(p.state, 'approved', p.fail_reason)
        self.assertFalse(p.purchase_order_id.picking_ids)
        self.assertEqual(p.invoice_id.state, 'paid')

    def test_links_back(self):
        st, p = self.aws()
        self.assertEqual(p.purchase_order_id.txn_proposal_id, p)
        self.assertEqual(p.invoice_id.txn_proposal_id, p)
