# Part of Odoo.

{
    'name': 'Budget Management',
    'category': 'Accounting/Accounting',
    'description': """
Use budgets to compare actual with expected revenues and costs
--------------------------------------------------------------
""",
    'depends': ['account_budget', 'purchase'],
    'data': [
        'views/account_analytic_account_views.xml',
        'views/budget_analytic_views.xml',
        'views/budget_line_view.xml',
        'views/purchase_views.xml',
        'reports/budget_report_view.xml',
    ],
    'demo': ['demo/account_budget_demo.xml'],
    'auto_install': True,
    'author': 'odoo',
    'license': 'LGPL-3',
}
