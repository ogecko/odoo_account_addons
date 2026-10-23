odoo.define('account_bank_statement_template.close_dialog', function (require) {
"use strict";

// A many2one link with options="{'close_dialog': True}" opens its record full
// page and closes the dialog it was clicked in. Without this, Odoo 10 opens the
// record behind the dialog (e.g. a proposal's template), where it can't be seen.

var Dialog = require('web.Dialog');
var Model = require('web.Model');
var FieldMany2One = require('web.form_relational').FieldMany2One;

FieldMany2One.include({
    execute_formview_action: function () {
        var dialog = this.getParent();
        while (dialog && !(dialog instanceof Dialog)) {
            dialog = dialog.getParent();
        }
        if (!this.options.close_dialog || !dialog) {
            return this._super.apply(this, arguments);
        }
        var self = this;
        var context = this.build_context().eval();
        return new Model(this.field.relation).call('get_formview_action', [[this.get('value')], context])
            .then(function (action) {
                return self.do_action(action);
            }).then(function () {
                dialog.close();
            });
    },
});

});
