# -*- coding: utf-8 -*-
{
    'name': 'Bank Statement Templates',

    'summary': "Match bank statement lines to templates and create the "
               "orders, invoices and reconciliations they stand for",

    'description': """
        Templates recognise recurring bank statement lines (text, direction,
        amount range) and say what to create from them: a sales order, a
        purchase order, or an entry straight to an account. Matching builds
        proposals on the statement; nothing is written until they are
        reviewed and approved.
    """,

    'category': 'Accounting',
    'version': '10.0.1.0.0',
    'author': "oGecko Solutions",
    'website': "http://www.ogecko.com",
    'license': 'AGPL-3',
    'depends': [
        'sale',
        'purchase',
        'account_bank_statement_import',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/txn_template_export.xml',
        'views/assets.xml',
        'views/txn_template_views.xml',
        'views/bank_statement_views.xml',
        'views/gst_consolidation_views.xml',
    ],
    'installable': True,
}
