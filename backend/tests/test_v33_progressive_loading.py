from __future__ import annotations

import subprocess

from fastapi.testclient import TestClient

from app.accounts import InMemoryAccountAccess
from app.http import create_app


def test_v33_workspace_assets_advertise_progressive_loading() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))

    shell = client.get("/workspace/canvases")
    gateway = client.get("/web-assets/saas-canvas-gateway.js")
    app = client.get("/web-assets/app.js")

    assert shell.status_code == 200
    assert "workspaceLoading=1" in shell.text
    assert gateway.status_code == 200
    assert "saas-canvas-hydrated" in gateway.text
    assert "data:image/gif;base64" in gateway.text
    assert app.status_code == 200
    assert "/thumbnail?size=512" in app.text
    assert "onPreview" in app.text


def test_v33_canvas_open_returns_saved_document_before_thumbnail_hydration() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    gateway = client.get("/web-assets/saas-canvas-gateway.js")
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
const stored = new Map();
const events = [];
let thumbnailReads = 0;
const canvasValue = {
  canvas_id: 'canvas-progressive', title: 'Progressive', kind: 'smart', version: 4,
  created_at: '2026-08-20T00:00:00Z', updated_at: '2026-08-20T00:00:00Z',
  document: {nodes: [{id: 'image-1', type: 'smart-image', images: [
    {media_id: 'media-1', kind: 'image', mime_type: 'image/png', url: '/api/v1/media/media-1/content'},
  ]}], connections: []},
};
function json(value) {
  return new Response(JSON.stringify(value), {status: 200, headers: {'Content-Type': 'application/json'}});
}
async function nativeFetch(input) {
  const path = String(input);
  if (path === '/api/v1/canvases/canvas-progressive') return json(canvasValue);
  if (path === '/api/v1/media/media-1/thumbnail?size=512') {
    thumbnailReads += 1;
    return new Response(new Uint8Array([1, 2, 3]), {status: 200, headers: {'Content-Type': 'image/png'}});
  }
  throw new Error(`unexpected request: ${path}`);
}
global.window = {
  location: {pathname: '/workspace/canvases/canvas-progressive/smart', origin: 'http://test', replace() {}},
  sessionStorage: {
    getItem(key) { return key === 'creative_studio_access_token' ? 'account-token' : (stored.get(key) || null); },
    setItem(key, value) { stored.set(key, value); },
    removeItem(key) { stored.delete(key); },
  },
  fetch: nativeFetch,
  addEventListener(name) { if (name === 'saas-canvas-hydrated') return; },
  dispatchEvent(event) { if (event.type === 'saas-canvas-hydrated') events.push(event); },
  CustomEvent: class CustomEvent { constructor(type, init) { this.type = type; this.detail = init?.detail; } },
  setTimeout,
  clearTimeout,
  crypto: {randomUUID() { return 'uuid'; }},
};
global.document = {getElementById() { return null; }, body: {appendChild() {}}, createElement() { return {}; }};
URL.createObjectURL = () => 'blob:thumb';
URL.revokeObjectURL = () => {};
(async () => {
  eval(source);
  const first = await window.fetch('/api/canvases/canvas-progressive');
  const firstCanvas = (await first.json()).canvas;
  assert.match(firstCanvas.nodes[0].images[0].thumbnail, /^data:image\/gif/);
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(thumbnailReads, 1);
  assert.equal(events.length, 1);
  assert.equal(events[0].detail.canvas.nodes[0].images[0].thumbnail, 'blob:thumb');

  const reopened = await window.fetch('/api/canvases/canvas-progressive');
  const reopenedCanvas = (await reopened.json()).canvas;
  assert.match(reopenedCanvas.nodes[0].images[0].thumbnail, /^data:image\/gif/);
})().catch(error => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
"""

    result = subprocess.run(
        ["node", "-e", harness],
        input=gateway.text,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
