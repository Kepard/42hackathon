import { readFile, writeFile, mkdir, readdir } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
const root = resolve(import.meta.dirname, '..');
const assets = {};
for (const name of await readdir(resolve(root, 'web'))) {
  if (name.startsWith('.')) continue;
  const file = await readFile(resolve(root, 'web', name));
  const type = name.endsWith('.html') ? 'text/html; charset=utf-8' : name.endsWith('.css') ? 'text/css; charset=utf-8' : name.endsWith('.js') ? 'application/javascript; charset=utf-8' : name.endsWith('.woff2') ? 'font/woff2' : 'text/plain; charset=utf-8';
  const path = name === 'index.html' ? '/' : '/assets/' + name;
  assets[path] = { type, data: file.toString('base64') };
}
const videoSource = (await readFile(resolve(root, 'worker/video.mjs'), 'utf8')).replace('export async function handleVideo', 'async function handleVideo');
const video = `const handleVideo = (() => {\n${videoSource}\nreturn handleVideo;\n})();\n`;
const api = video + (await readFile(resolve(root, 'worker/api.mjs'), 'utf8')).replace('import {handleVideo} from "./video.mjs";\n', '');
const entry = `${api}\nconst ASSETS = ${JSON.stringify(assets)};\nexport default { async fetch(request, env) {\n  const path = new URL(request.url).pathname;\n  if(path.startsWith('/api/')) { try { return await handleApi(request, env) || new Response('Not found', {status:404}); } catch { return new Response(JSON.stringify({error:'Service temporarily unavailable'}), {status:503, headers:{'content-type':'application/json','cache-control':'no-store'}}); } }\n  if(!['GET','HEAD'].includes(request.method)) return new Response('Method not allowed',{status:405});\n  const asset=ASSETS[path === '/index.html' ? '/' : path];\n  if(!asset) return new Response('Not found',{status:404});\n  const bytes=Uint8Array.from(atob(asset.data), c=>c.charCodeAt(0));\n  return new Response(request.method === 'HEAD' ? null : bytes, {headers:{'content-type':asset.type,'cache-control':path.endsWith('.woff2')?'public, max-age=86400':'no-cache','x-content-type-options':'nosniff','referrer-policy':'same-origin','content-security-policy':\"default-src 'self'; img-src 'self' data: blob:; font-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self' wss://gladia.io wss://*.gladia.io; media-src 'self' blob:; worker-src 'self' blob:; frame-ancestors 'self' https://chatgpt.com\"}});\n} };\n`;
await mkdir(resolve(root,'dist/server'),{recursive:true});
await mkdir(resolve(root,'dist/.openai'),{recursive:true});
await writeFile(resolve(root,'dist/server/index.js'),entry);
await writeFile(resolve(root,'dist/.openai/hosting.json'),await readFile(resolve(root,'.openai/hosting.json')));
console.log(`Built Worker with ${Object.keys(assets).length} embedded assets.`);
