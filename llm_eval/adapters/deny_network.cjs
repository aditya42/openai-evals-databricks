'use strict';

// Replay assertions only read local responses. Reject network calls before a
// socket opens, including optional vendor metadata and telemetry requests.
const deny = () => {
  const error = new Error('Outbound network is disabled for cached evaluation');
  error.code = 'ERR_EVAL_NETWORK_DISABLED';
  throw error;
};
const net = require('node:net');
net.connect = deny;
net.createConnection = deny;
net.Socket.prototype.connect = deny;
for (const name of ['node:http', 'node:https']) {
  const module = require(name);
  module.request = deny;
  module.get = deny;
}
if (globalThis.fetch) globalThis.fetch = async () => deny();
require('node:module').syncBuiltinESMExports();
