from matching import (AMBIGUOUS, NO_MATCH, OUT_OF_RANGE, classify, normalize,
                      text_matches)

from datetime import date


def test_imports():
    import matching  # noqa: F401


def line(id, amount, name, d=date(2026, 7, 1)):
    return {'id': id, 'date': d, 'amount': amount, 'name': name}


def tmpl(id, name, match_text, amount_min, amount_max, direction='in', is_regex=False):
    return {'id': id, 'name': name, 'direction': direction,
            'match_text': match_text, 'match_is_regex': is_regex,
            'amount_min': amount_min, 'amount_max': amount_max,
            'companion_text': None, 'companion_is_regex': False,
            'fee_pct_min': 2.0, 'fee_pct_max': 4.0}


def test_normalize_spacing_case_and_ofx_prefix():
    assert normalize(':  Transfer from Jane  Citizen ') == 'transfer from jane citizen'


def test_substring_match_ignores_case_and_spacing():
    assert text_matches('JANE CITIZEN', False, ': Transfer from Jane  Citizen NetBank')


def test_regex_match():
    assert text_matches(r'sinch mailgun .* usd', True,
                        ': SINCH MAILGUN SAN ANTONIO TX USA Card xx1 USD 3.30')


def test_classify_in_range_match():
    r = classify([line(1, 22.0, 'Pay from Acme Pty')], [tmpl(10, 'Acme', 'acme', 20, 30)])
    assert r['matched'] == {10: [1]}
    assert r['manual'] == {}


def test_classify_direction_must_agree():
    r = classify([line(1, -22.0, 'Pay to Acme Pty')], [tmpl(10, 'Acme', 'acme', 20, 30)])
    assert r['matched'] == {}
    assert r['manual'][1][0] == NO_MATCH


def test_classify_out_of_range():
    r = classify([line(1, 45.0, 'Pay from Acme Pty')], [tmpl(10, 'Acme', 'acme', 20, 30)])
    assert r['matched'] == {}
    assert r['manual'][1] == (OUT_OF_RANGE, 'Acme')


def test_classify_range_bounds_inclusive():
    r = classify([line(1, 20.0, 'Acme'), line(2, 30.0, 'Acme')],
                 [tmpl(10, 'Acme', 'acme', 20, 30)])
    assert r['matched'] == {10: [1, 2]}


def test_classify_out_of_range_but_other_in_range_matches():
    r = classify([line(1, 45.0, 'Acme')],
                 [tmpl(10, 'A', 'acme', 20, 30), tmpl(11, 'B', 'acme', 40, 50)])
    assert r['matched'] == {11: [1]}
    assert r['manual'] == {}


def test_classify_two_in_range_is_ambiguous():
    r = classify([line(1, 25.0, 'Acme')],
                 [tmpl(10, 'Tmpl A', 'acme', 20, 30), tmpl(11, 'Tmpl B', 'acme', 20, 30)])
    assert r['matched'] == {}
    assert r['manual'][1] == (AMBIGUOUS, 'Tmpl A, Tmpl B')


def test_classify_no_text_match():
    r = classify([line(1, 25.0, 'Something else')], [tmpl(10, 'Acme', 'acme', 20, 30)])
    assert r['manual'][1] == (NO_MATCH, '')


# --- Task 3: fee (companion) pairing -------------------------------------

from matching import FEE_UNPAIRED, foreign_amount, value_date


def mailgun_tmpl(id=20, fee_min=2.0, fee_max=4.0):
    t = tmpl(id, 'Mailgun', 'mailgun', 0.5, 100, direction='out')
    t.update({'companion_text': 'international transaction fee',
              'fee_pct_min': fee_min, 'fee_pct_max': fee_max})
    return t


def charge(id, amount, vd='04/03/2026', usd='3.30', who='SINCH MAILGUN SAN ANTONIO TX USA'):
    return line(id, amount, ': %s Card xx1234 USD %s Value Date: %s' % (who, usd, vd))


def fee(id, amount, vd='04/03/2026'):
    return line(id, amount, ': International Transaction Fee Value Date: %s' % vd)


def test_value_date_parsed():
    assert value_date('X Value Date: 04/03/2026') == date(2026, 3, 4)
    assert value_date('no date here') is None


def test_foreign_amount_ignores_aud():
    assert foreign_amount(': Udemy Card xx1 AUD 25.00 Value Date: 01/05/2026') is None
    assert foreign_amount(': MAILGUN Card xx1 USD 2.10 Value Date: 04/03/2026') == ('USD', 2.10)


def test_fee_pairs_with_single_foreign_primary_same_value_date():
    lines = [charge(1, -5.00), fee(2, -0.15)]
    r = classify(lines, [mailgun_tmpl()])
    assert r['matched'] == {20: [1]}
    assert r['companions'] == {20: [(2, 1)]}
    assert r['manual'] == {}


def test_fee_unpaired_when_two_foreign_candidates():
    lines = [charge(1, -5.00), charge(3, -6.00, who='OTHER SHOP NY USA', usd='4.00'), fee(2, -0.15)]
    r = classify(lines, [mailgun_tmpl()])
    assert r['manual'][2][0] == FEE_UNPAIRED
    assert r['companions'] == {}


def test_fee_unpaired_when_candidate_not_in_template():
    lines = [charge(3, -6.00, who='OTHER SHOP NY USA', usd='4.00'), fee(2, -0.15)]
    r = classify(lines, [mailgun_tmpl()])
    assert r['manual'][2][0] == FEE_UNPAIRED


def test_fee_unpaired_outside_pct_bounds():
    r = classify([charge(1, -5.00), fee(2, -0.30)], [mailgun_tmpl()])
    assert r['manual'][2][0] == FEE_UNPAIRED
    assert r['matched'] == {20: [1]}


def test_fee_at_pct_bound_pairs():
    r = classify([charge(1, -4.00), fee(2, -0.08)], [mailgun_tmpl()])
    assert r['companions'] == {20: [(2, 1)]}


def test_fee_candidates_come_from_context_lines():
    # A reconciled foreign charge on the same value date is not eligible for
    # matching, but still makes the fee ambiguous.
    other = charge(3, -6.00, who='OTHER SHOP NY USA', usd='4.00')
    lines = [charge(1, -5.00), fee(2, -0.15)]
    r = classify(lines, [mailgun_tmpl()], context_lines=lines + [other])
    assert r['manual'][2][0] == FEE_UNPAIRED


def test_fee_line_never_matches_as_primary():
    catch_all = tmpl(30, 'Bank fees', 'fee', 0.01, 10, direction='out')
    r = classify([charge(1, -5.00), fee(2, -0.15)], [mailgun_tmpl(), catch_all])
    assert r['companions'] == {20: [(2, 1)]}
    assert 30 not in r['matched']


def test_fee_matching_two_companion_templates_is_ambiguous():
    r = classify([charge(1, -5.00), fee(2, -0.15)], [mailgun_tmpl(20), mailgun_tmpl(21)])
    assert r['manual'][2][0] == AMBIGUOUS


def test_fee_pairs_when_value_date_is_day_before_charge():
    # The bank dates the fee one day before its charge.
    r = classify([fee(2, -0.18, vd='01/06/2025'), charge(1, -6.00, vd='02/06/2025', usd='3.90')],
                 [mailgun_tmpl()])
    assert r['companions'] == {20: [(2, 1)]}


def test_fee_unpaired_when_charge_three_days_away():
    r = classify([fee(2, -0.18, vd='01/06/2025'), charge(1, -6.00, vd='04/06/2025', usd='3.90')],
                 [mailgun_tmpl()])
    assert r['manual'][2][0] == FEE_UNPAIRED


# --- Task 4: grouping, prices, quarters, descriptions --------------------

from datetime import timedelta

from matching import ex_tax_unit, group_by_amount, quarter_of, render_description, same_quarter


def test_group_by_amount_mixed_prices():
    lines = [line(1, 33.00, 'weekly', date(2026, 1, 1))]
    lines += [line(10 + i, 44.00, 'weekly', date(2026, 1, 8) + timedelta(weeks=i)) for i in range(12)]
    groups = group_by_amount(list(reversed(lines)))
    assert [(a, len(ls)) for a, ls in groups] == [(33.0, 1), (44.0, 12)]
    assert [l['date'] for l in groups[1][1]] == sorted(l['date'] for l in groups[1][1])


def test_group_by_amount_uses_absolute_amount():
    groups = group_by_amount([line(1, -3.20, 'x'), line(2, -3.20, 'x', date(2026, 7, 2))])
    assert [(a, len(ls)) for a, ls in groups] == [(3.2, 2)]


def test_ex_tax_unit_exact():
    assert ex_tax_unit(44.00, 10.0) == 40.0
    assert ex_tax_unit(33.00, 10.0) == 30.0


def test_quarter_of():
    assert quarter_of(date(2026, 6, 30)) == '2026Q2'
    assert quarter_of(date(2026, 7, 1)) == '2026Q3'


def test_same_quarter_false_across_boundary():
    assert not same_quarter([date(2026, 6, 30), date(2026, 7, 1)])
    assert same_quarter([date(2026, 7, 1), date(2026, 9, 30)])


def test_render_weekly_description():
    dates = [date(2026, 4, 2) + timedelta(weeks=i) for i in range(13)]
    assert render_description('Weekly Service {year} (Wk{first_week} to Wk{last_week})',
                              dates, date(2026, 6, 25)) == 'Weekly Service 2026 (Wk14 to Wk26)'


def test_render_quarter_and_date():
    # {quarter} follows the proposal; {date} is the bank line's own date.
    assert render_description('{quarter} {date}', [date(2026, 7, 3)], date(2026, 9, 4)) \
        == '2026Q3 03/07/2026'


def test_render_date_is_last_line_of_group():
    assert render_description('{date}', [date(2026, 7, 3), date(2026, 8, 5)], date(2026, 9, 4)) \
        == '05/08/2026'


def test_render_unknown_placeholder_kept():
    assert render_description('{foo} x', [date(2026, 7, 3)], date(2026, 7, 3)) == '{foo} x'
