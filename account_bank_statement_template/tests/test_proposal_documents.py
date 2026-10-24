# -*- coding: utf-8 -*-
import base64

from odoo.tests.common import at_install, post_install

from .common import TxnCase, D


@at_install(False)
@post_install(True)
class TestProposalDocuments(TxnCase):

    def setUp(self):
        super(TestProposalDocuments, self).setUp()
        self.env['account.bank.txn.template'].search([]).write({'active': False})
        self.make_template(name='Interest', match_text='credit interest', amount_min=1.0, amount_max=500.0,
                           journal_id=self.journal.id)
        self.env.cr.execute('UPDATE res_company SET fiscalyear_lock_date = NULL WHERE id = %s',
                            (self.env.user.company_id.id,))
        self.env.user.company_id.invalidate_cache()
        self.st = self.make_statement([(D(2026, 7, 1), 12.34, 'Credit Interest')])
        self.st.action_match_templates()
        self.p = self.st.txn_proposal_ids

    def attach(self, proposal, name='statement.pdf'):
        doc = self.env['ir.attachment'].create({
            'name': name, 'datas_fname': name, 'datas': base64.b64encode(b'%PDF-1.4 made up'),
            'res_model': proposal._name, 'res_id': proposal.id})
        proposal.write({'attachment_ids': [(4, doc.id)]})
        return doc

    def test_attach_counts(self):
        self.attach(self.p)
        self.attach(self.p, 'second.pdf')
        self.assertEqual(self.p.attachment_count, 2)

    def test_open_proposal_with_document_survives_generate(self):
        doc = self.attach(self.p)
        self.assertTrue(self.p.edited)
        self.st.action_match_templates()
        self.assertTrue(self.p.exists())
        self.assertEqual(self.p.attachment_ids, doc)

    def test_attach_after_approval(self):
        self.p.action_approve()
        self.assertEqual(self.p.state, 'approved')
        doc = self.attach(self.p)
        self.assertEqual(self.p.attachment_ids, doc)

    def test_upload_without_record_id_is_linked(self):
        """The upload widget in the dialog stores res_id 0; saving links it."""
        doc = self.env['ir.attachment'].create({
            'name': 'x.pdf', 'datas_fname': 'x.pdf', 'datas': base64.b64encode(b'%PDF-1.4 made up'),
            'res_model': self.p._name, 'res_id': 0})
        self.p.write({'attachment_ids': [(6, 0, [doc.id])]})
        self.assertEqual((doc.res_model, doc.res_id), (self.p._name, self.p.id))
        self.p.action_discard()
        self.assertFalse(doc.exists())

    def test_discard_removes_documents(self):
        doc = self.attach(self.p)
        self.p.action_discard()
        self.assertFalse(doc.exists())

    def test_form_and_list_show_documents(self):
        form = self.env['account.bank.txn.proposal'].fields_view_get(view_type='form')['arch']
        self.assertIn('widget="many2many_binary"', form)
        st_view = self.env['account.bank.statement'].fields_view_get(view_type='form')
        tree = st_view['fields']['txn_proposal_ids']['views']['tree']['arch']
        self.assertIn('attachment_count', tree)
