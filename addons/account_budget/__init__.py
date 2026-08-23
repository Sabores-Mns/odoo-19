# -*- coding: utf-8 -*-
# Part of Odoo.

from . import models
from . import reports
from . import wizards


def post_init_hook(env):
    """ Create analytic plan field on budget analytic line and report for existing plans """
    env['account.analytic.plan'].search([])._sync_all_plan_column()
