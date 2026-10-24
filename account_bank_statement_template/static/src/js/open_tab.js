odoo.define('account_bank_statement_template.open_tab', function (require) {
"use strict";

// A form opened with context {'open_tab': '<page name>'} starts on that
// notebook page instead of the first one (Odoo 10's own <page autofocus>).

var FormRenderingEngine = require('web.FormRenderingEngine');

FormRenderingEngine.include({
    process_notebook: function ($notebook) {
        var dataset = this.view && this.view.dataset;
        var tab = dataset && dataset.context && dataset.context.open_tab;
        if (tab) {
            $notebook.find('> page[name="' + tab + '"]').attr('autofocus', 'autofocus');
        }
        return this._super.apply(this, arguments);
    },
});

});
