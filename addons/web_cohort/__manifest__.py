# -*- coding: utf-8 -*-
# Part of Odoo.

{
    'name': 'Cohort View',
    'summary': 'Basic Cohort view for odoo',
    'category': 'Hidden',
    'depends': ['web'],
    'assets': {
        'web.assets_backend_lazy': [
            'web_cohort/static/src/**/*',
        ],
        'web.assets_unit_tests': [
            'web_cohort/static/tests/**/*.js',
        ],
    },
    'auto_install': True,
    'author': 'odoo',
    'license': 'LGPL-3',
}
