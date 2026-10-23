# -*- coding: utf-8 -*-
import logging

from odoo import api, fields, models

from ..lib import matching

_logger = logging.getLogger(__name__)

REASON_LABELS = {
    matching.NO_MATCH: 'no match',
    matching.OUT_OF_RANGE: 'out of range',
    matching.AMBIGUOUS: 'ambiguous',
    matching.FEE_UNPAIRED: 'fee unpaired',
}


class AccountBankStatementLine(models.Model):
    _inherit = 'account.bank.statement.line'

    txn_proposal_id = fields.Many2one('account.bank.txn.proposal', string='Proposal',
                                      ondelete='set null', copy=False, index=True)
    txn_manual = fields.Boolean(string='Manual', copy=False,
                                help="Removed from a proposal by hand: matching skips it.")
    txn_manual_reason = fields.Char(string='Why manual', copy=False)

    def _to_match_dict(self):
        self.ensure_one()
        return {'id': self.id, 'date': fields.Date.from_string(self.date),
                'amount': self.amount, 'name': self.name or ''}


class AccountBankStatement(models.Model):
    _inherit = 'account.bank.statement'

    txn_proposal_ids = fields.One2many('account.bank.txn.proposal', 'statement_id',
                                       string='Proposals')
    txn_manual_line_ids = fields.One2many('account.bank.statement.line',
                                          compute='_compute_txn_manual_line_ids',
                                          string='Lines to do by hand')

    @api.depends('line_ids.journal_entry_ids', 'line_ids.txn_proposal_id')
    def _compute_txn_manual_line_ids(self):
        for st in self:
            st.txn_manual_line_ids = st.line_ids.filtered(
                lambda l: not l.journal_entry_ids and not l.txn_proposal_id)

    @api.multi
    def action_match_templates(self):
        for st in self:
            st._txn_match()
        return True

    def _txn_match(self):
        self.ensure_one()
        stale = self.txn_proposal_ids.filtered(
            lambda p: p.state == 'failed' or (p.state == 'open' and not p.edited))
        for p in stale.filtered(lambda p: p.state == 'failed'):
            _logger.info('Rebuilding failed proposal %s (%s): %s', p.id, p.template_id.name,
                         p.fail_reason)
        stale.unlink()

        eligible = self.line_ids.filtered(
            lambda l: not l.journal_entry_ids and not l.txn_proposal_id and not l.txn_manual)
        templates = self.env['account.bank.txn.template'].search(
            [('journal_id', 'in', [False, self.journal_id.id])])
        result = matching.classify(
            [l._to_match_dict() for l in eligible],
            [t._to_match_dict() for t in templates],
            context_lines=[l._to_match_dict() for l in self.line_ids])

        Line = self.env['account.bank.statement.line']
        for line_id, (code, detail) in result['manual'].items():
            label = REASON_LABELS[code]
            Line.browse(line_id).txn_manual_reason = '%s: %s' % (label, detail) if detail else label

        builder = self.env['account.bank.txn.proposal'].with_context(txn_builder=True)
        for template in templates:
            line_ids = result['matched'].get(template.id)
            if not line_ids:
                continue
            lines = Line.browse(line_ids)
            fees = dict(result['companions'].get(template.id, []))  # fee id -> charge id
            if template.kind == 'direct':
                for line in lines:
                    builder._build(self, template, line)
            else:
                builder._build(self, template, lines, Line.browse(list(fees)), fees)
        (eligible - Line.browse(list(result['manual']))).write({'txn_manual_reason': False})
