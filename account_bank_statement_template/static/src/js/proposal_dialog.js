odoo.define('account_bank_statement_template.proposal_dialog', function (require) {
"use strict";

// A one2many with options="{'open_action': '<method>'}" opens a clicked row
// through that method's action (an editable dialog) instead of Odoo 10's
// read-only row dialog, and reloads its form when the dialog closes. Being an
// action dialog, it also closes when a link in it opens a page.

var core = require('web.core');
var Model = require('web.Model');
var One2ManyListView = core.one2many_view_registry.get('list');

One2ManyListView.include({
    do_activate_record: function (index, id) {
        var x2m = this.x2m;
        if (!x2m.options.open_action || !_.isNumber(id)) {
            return this._super.apply(this, arguments);
        }
        var form = x2m.view;
        var context = x2m.build_context().eval();
        return new Model(x2m.field.relation).call(x2m.options.open_action, [[id], context])
            .then(function (action) {
                return form.do_action(action, {
                    on_close: function () {
                        if (form.get('actual_mode') === 'view') {
                            form.reload();
                        }
                    },
                });
            });
    },
});

});
