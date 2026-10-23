# -*- coding: utf-8 -*-
from odoo import fields, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'
    txn_proposal_id = fields.Many2one('account.bank.txn.proposal', string='Bank proposal',
                                      readonly=True, copy=False, index=True)


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'
    txn_proposal_id = fields.Many2one('account.bank.txn.proposal', string='Bank proposal',
                                      readonly=True, copy=False, index=True)


class AccountInvoice(models.Model):
    _inherit = 'account.invoice'
    txn_proposal_id = fields.Many2one('account.bank.txn.proposal', string='Bank proposal',
                                      readonly=True, copy=False, index=True)


class AccountMove(models.Model):
    _inherit = 'account.move'
    txn_proposal_id = fields.Many2one('account.bank.txn.proposal', string='Bank proposal',
                                      readonly=True, copy=False, index=True)
