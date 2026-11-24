import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.dirname(fileURLToPath(import.meta.url));
const port = Number(process.env.UI_PORT || 5173);
const backend = new URL(process.env.BACKEND_URL || 'http://127.0.0.1:8000');
const types = { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.json': 'application/json' };
const agent = new http.Agent({ keepAlive: true });

const server = http.createServer((req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`);
  if (url.pathname === '/local/info') {
    const infoPath = path.join(root, 'runtime.json');
    let info = {};
    try { if (fs.existsSync(infoPath)) info = JSON.parse(fs.readFileSync(infoPath, 'utf8')); } catch { /* A launcher may be refreshing the local config. */ }
    res.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
    res.end(JSON.stringify({ ...info, backend: backend.origin, ui: `http://127.0.0.1:${port}` }));
    return;
  }
  if (url.pathname === '/api' || url.pathname.startsWith('/api/')) {
    const rawPath = url.pathname.slice(4) || '/';
    let targetPath = rawPath;
    if (rawPath === '/chat' || rawPath.startsWith('/chat/')) {
      targetPath = '/api/v1' + rawPath;
    } else if (rawPath === '/documents' || rawPath.startsWith('/documents/')) {
      targetPath = '/api/v1' + rawPath;
    } else if (rawPath === '/admin' || rawPath.startsWith('/admin/')) {
      targetPath = '/api/v1' + rawPath;
    }

    const headers = { ...req.headers, host: backend.host };
    delete headers.connection;
    if (!headers['x-admin-api-key'] && !headers['X-Admin-API-Key']) {
      headers['X-Admin-API-Key'] = process.env.ADMIN_API_KEY || 'admin:admin@123';
    }

    const upstream = http.request({
      hostname: backend.hostname, port: backend.port || 80,
      path: targetPath + url.search, method: req.method, headers, agent,
      timeout: 600000,
    }, incoming => {
      const responseHeaders = { ...incoming.headers, 'cache-control': 'no-store' };
      delete responseHeaders.connection;
      res.writeHead(incoming.statusCode, responseHeaders);
      incoming.pipe(res);
      incoming.on('error', () => res.destroy());
    });
    upstream.on('timeout', () => upstream.destroy(new Error('Backend request timed out')));
    upstream.on('error', error => {
      if (!res.headersSent) {
        res.writeHead(502, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ detail: `Cannot reach Docker API at ${backend.origin}: ${error.message}` }));
      } else res.destroy();
    });
    req.on('aborted', () => upstream.destroy());
    res.on('close', () => { if (!res.writableEnded) upstream.destroy(); });
    req.pipe(upstream);
    return;
  }
  let filename;
  try { filename = url.pathname === '/' ? 'index.html' : decodeURIComponent(url.pathname).slice(1); }
  catch { res.writeHead(400); res.end('Invalid path'); return; }
  const target = path.resolve(root, filename);
  if (!target.startsWith(root + path.sep) || !['index.html', 'styles.css', 'app.js'].includes(filename)) {
    res.writeHead(404); res.end('Not found'); return;
  }
  fs.readFile(target, (error, data) => {
    if (error) { res.writeHead(404); res.end('Not found'); return; }
    res.writeHead(200, { 'Content-Type': `${types[path.extname(target)]}; charset=utf-8`, 'Cache-Control': 'no-store' });
    res.end(data);
  });
});
server.requestTimeout = 600000;
server.timeout = 0;
server.listen(port, '127.0.0.1', () => console.log(`NursingBot local UI: http://127.0.0.1:${port} → ${backend.origin}`));
