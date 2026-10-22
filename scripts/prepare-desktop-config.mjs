import fs from 'node:fs';
import path from 'node:path';

const apiBaseUrl = process.env.DESKTOP_API_BASE_URL || 'http://127.0.0.1:8000/api/v1';
const wsUrl = process.env.DESKTOP_WS_URL || apiBaseUrl.replace(/^http/, 'ws') + '/realtime';
if (process.env.DESKTOP_RELEASE === 'true' && (!apiBaseUrl.startsWith('https://') || !wsUrl.startsWith('wss://'))) {
  throw new Error('Release desktop builds require HTTPS API and WSS realtime URLs');
}
const output = path.resolve('apps/desktop/public-config.json');
fs.writeFileSync(output, JSON.stringify({apiBaseUrl, wsUrl}, null, 2) + '\n');
console.log('Desktop public endpoints prepared:', new URL(apiBaseUrl).origin);
