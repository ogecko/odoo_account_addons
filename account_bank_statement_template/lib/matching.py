# -*- coding: utf-8 -*-
"""Bank line matching rules, kept free of Odoo imports so they can be unit
tested outside Odoo. Must stay importable on Python 2.7 and 3."""
from __future__ import absolute_import, division, unicode_literals

import re
from datetime import date, timedelta

# Reasons a bank line is left for manual handling: (code, detail text).
NO_MATCH = 'no_match'
OUT_OF_RANGE = 'out_of_range'
AMBIGUOUS = 'ambiguous'
FEE_UNPAIRED = 'fee_unpaired'

# The bank's value date on a fee can differ from its charge's by a day.
FEE_VALUE_DATE_WINDOW = timedelta(days=2)

_VALUE_DATE = re.compile(r'Value Date: (\d{2})/(\d{2})/(\d{4})')
_FOREIGN_AMOUNT = re.compile(r'\b([A-Z]{3}) (\d+\.\d{2})\b')


def normalize(text):
    """Lowercase, collapse whitespace, and drop the ': ' the OFX import puts
    in front of a description with no payee."""
    text = re.sub(r'\s+', ' ', text or '').strip().lower()
    if text.startswith(':'):
        text = text[1:].strip()
    return text


def text_matches(pattern, is_regex, name):
    if not pattern:
        return False
    if is_regex:
        return re.search(pattern, normalize(name), re.I) is not None
    return normalize(pattern) in normalize(name)


def _direction_ok(template, amount):
    return amount > 0 if template['direction'] == 'in' else amount < 0


def _in_range(template, amount):
    return template['amount_min'] <= abs(amount) <= template['amount_max']


def value_date(name):
    m = _VALUE_DATE.search(name or '')
    if not m:
        return None
    day, month, year = (int(g) for g in m.groups())
    return date(year, month, day)


def foreign_amount(name):
    """(currency, amount) of the first non-AUD currency amount in a card
    line's description, e.g. 'USD 2.10'."""
    for currency, amount in _FOREIGN_AMOUNT.findall(name or ''):
        if currency != 'AUD':
            return currency, float(amount)
    return None


def _companion_templates(templates, line):
    return [t for t in templates
            if t.get('companion_text')
            and text_matches(t['companion_text'], t.get('companion_is_regex'), line['name'])]


def classify(lines, templates, context_lines=None):
    """Assign each line to at most one template.

    context_lines (default: lines) are all lines of the statement, reconciled
    or not; they supply the candidate charges a fee could belong to.

    Returns {'matched': {template_id: [line_id, ...]},
             'companions': {template_id: [(fee_line_id, primary_line_id), ...]},
             'manual': {line_id: (reason_code, detail_text)}}.
    """
    if context_lines is None:
        context_lines = lines
    matched = {}
    companions = {}
    manual = {}
    fees = []
    for line in lines:
        owners = _companion_templates(templates, line)
        if len(owners) == 1:
            fees.append((line, owners[0]))
            continue
        if owners:
            manual[line['id']] = (AMBIGUOUS, ', '.join(t['name'] for t in owners))
            continue
        candidates = [t for t in templates
                      if _direction_ok(t, line['amount'])
                      and text_matches(t['match_text'], t['match_is_regex'], line['name'])]
        in_range = [t for t in candidates if _in_range(t, line['amount'])]
        if len(in_range) == 1:
            matched.setdefault(in_range[0]['id'], []).append(line['id'])
        elif in_range:
            manual[line['id']] = (AMBIGUOUS, ', '.join(t['name'] for t in in_range))
        elif candidates:
            manual[line['id']] = (OUT_OF_RANGE, ', '.join(t['name'] for t in candidates))
        else:
            manual[line['id']] = (NO_MATCH, '')

    for line, template in fees:
        primary = _fee_primary(line, template, context_lines, matched)
        if primary is None:
            manual[line['id']] = (FEE_UNPAIRED, template['name'])
        else:
            companions.setdefault(template['id'], []).append((line['id'], primary['id']))
    return {'matched': matched, 'companions': companions, 'manual': manual}


def _fee_primary(fee, template, context_lines, matched):
    """The one charge this fee belongs to, or None if that isn't certain."""
    vd = value_date(fee['name'])
    if vd is None:
        return None
    candidates = [l for l in context_lines
                  if l['id'] != fee['id']
                  and value_date(l['name']) is not None
                  and abs(value_date(l['name']) - vd) <= FEE_VALUE_DATE_WINDOW
                  and foreign_amount(l['name'])]
    if len(candidates) != 1:
        return None
    primary = candidates[0]
    if primary['id'] not in matched.get(template['id'], []):
        return None
    pct = round(abs(fee['amount']) / abs(primary['amount']) * 100, 4)
    if not template['fee_pct_min'] <= pct <= template['fee_pct_max']:
        return None
    return primary


def group_by_amount(lines):
    """[(amount, [line, ...]), ...] grouped on the absolute amount; groups in
    order of their earliest date, lines in date order."""
    groups = {}
    for line in sorted(lines, key=lambda l: (l['date'], l['id'])):
        groups.setdefault(round(abs(line['amount']), 2), []).append(line)
    return sorted(groups.items(), key=lambda g: g[1][0]['date'])


def ex_tax_unit(amount, rate_pct):
    """Ex-tax unit price for a tax-inclusive bank amount. Odoo verifies that
    the price plus its tax gives back the bank amount."""
    return round(abs(amount) * 100 / (100 + rate_pct), 2)


def quarter_of(d):
    return '%dQ%d' % (d.year, (d.month - 1) // 3 + 1)


def same_quarter(dates):
    return len(set(quarter_of(d) for d in dates)) <= 1


def franking_credit(franked, company_tax_rate):
    """Franking credit on a franked dividend: the company tax already paid on
    it, franked / (1 - rate) - franked."""
    rate = company_tax_rate / 100.0
    return round(franked * rate / (1 - rate) + 1e-9, 2)


def prior_period_end(d):
    """Last day of the quarter before d's (e.g. the BAS period a payment is for)."""
    return date(d.year, (d.month - 1) // 3 * 3 + 1, 1) - timedelta(days=1)


def period_end(d):
    """Last day of d's quarter."""
    return prior_period_end(prior_period_end(d) + timedelta(days=100))


def split_amount(name, keyword):
    """The amount written after keyword in a bank description, e.g. 12.0 for
    'GST 12' or 'GST12'; None if the keyword isn't followed by an amount."""
    m = re.search(r'(?<![a-z0-9])%s\s*(\d+(?:\.\d{1,2})?)\b' % re.escape(keyword.strip()),
                  name or '', re.I)
    return float(m.group(1)) if m else None


_PLACEHOLDER = re.compile(r'\{(\w+)\}')


def render_description(pattern, line_dates, proposal_date):
    """Fill {year} {first_week} {last_week} {quarter} {date} {period_end}
    {prior_period_end} in a template's line description; unknown placeholders
    are left as written. {date} is the line's own bank date (the last one, for
    a grouped line); {period_end} the end of its quarter, {prior_period_end}
    the end of the quarter before."""
    values = {
        'year': '%d' % proposal_date.year,
        'first_week': '%02d' % min(line_dates).isocalendar()[1],
        'last_week': '%02d' % max(line_dates).isocalendar()[1],
        'quarter': quarter_of(proposal_date),
        'date': max(line_dates).strftime('%d/%m/%Y'),
        'period_end': period_end(max(line_dates)).strftime('%d %b %Y'),
        'prior_period_end': prior_period_end(max(line_dates)).strftime('%d %b %Y'),
    }
    return _PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), pattern or '')
