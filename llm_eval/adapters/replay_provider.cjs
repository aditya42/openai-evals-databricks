const fs = require('node:fs');

module.exports = class ReplayProvider {
  constructor(options) {
    this.config = options.config;
    this.rows = JSON.parse(fs.readFileSync(this.config.responses, 'utf8')).responses;
  }
  id() { return 'cached-openai-response'; }
  async callApi(prompt, context) {
    const row = this.rows[context.vars.case_id];
    if (!row || row.status !== 'ok') return { error: 'Generation unavailable' };
    return { output: row.output.trim() };
  }
};
