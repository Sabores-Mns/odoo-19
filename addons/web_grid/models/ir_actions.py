# -*- coding: utf-8 -*-
# Part of Odoo.

from odoo import fields, models


class IrActionsAct_WindowView(models.Model):
    _inherit = 'ir.actions.act_window.view'

    view_mode = fields.Selection(selection_add=[('grid', "Grid")], ondelete={'grid': 'cascade'})
