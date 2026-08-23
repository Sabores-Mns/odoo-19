# -*- coding: utf-8 -*-
# Part of Odoo.

from odoo import fields, models


class AccountAnalyticAccount(models.Model):
    _inherit = "account.analytic.account"

    budget_line_ids = fields.One2many('budget.line', 'auto_account_id', readonly=False)

    def _get_view(self, view_id=None, view_type='form', **options):
        arch, view = super()._get_view(view_id, view_type, **options)
        return self.env['analytic.plan.fields.mixin']._patch_view(arch, view, view_type)  # patch the budget line list view
