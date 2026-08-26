from __future__ import annotations

import subprocess

from fastapi.testclient import TestClient

from app.accounts import InMemoryAccountAccess
from app.http import create_app


def test_v34_image_history_hydrates_newest_task_and_image_first() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    script = client.get("/web-assets/app.js")
    start = script.text.index("async function restoreRecentImageResults")
    end = script.text.index("function isGptImage2ModelName", start)
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
const state = {route:'/workspace/images', imageSessionEntries:[], imageHistoryLoading:false, imageHistoryHydrated:false};
const order = [];
const renders = [];
const optionalApi = async path => {
  if (path === '/api/v1/generation-tasks/recent?limit=100') return [
    {task_id:'task-new', canvas_id:null, status:'succeeded', quantity:2, logical_model:'new', created_at:'2026-08-20T00:00:00Z'},
    {task_id:'task-old', canvas_id:null, status:'succeeded', quantity:1, logical_model:'old', created_at:'2026-08-19T00:00:00Z'},
  ];
  if (path === '/api/v1/generation-tasks/task-new/media') return [
    {media_id:'new-1', state:'temporary', created_at:'2026-08-20T00:02:00Z'},
    {media_id:'new-2', state:'temporary', created_at:'2026-08-20T00:01:00Z'},
  ];
  if (path === '/api/v1/generation-tasks/task-old/media') return [
    {media_id:'old-1', state:'temporary', created_at:'2026-08-19T00:01:00Z'},
  ];
  throw new Error(`unexpected request: ${path}`);
};
const clearImagePreviewUrls = () => {};
const isImageWorkspaceRoute = () => true;
const renderImageSessionResults = () => renders.push(state.imageSessionEntries.flatMap(e => e.media.map(m => m.media_id)));
const observeImageSessionTask = () => { throw new Error('historical succeeded task must not be observed'); };
const authenticatedMediaObjectUrl = async item => { order.push(item.media_id); return `blob:${item.media_id}`; };
const escapeHTML = value => String(value ?? '');
    const start = source.indexOf('async function restoreRecentImageResults');
    const end = source.indexOf('function isGptImage2ModelName', start);
    eval(source.slice(start, end));
(async () => {
  await restoreRecentImageResults();
  assert.deepEqual(order, ['new-1','new-2','old-1']);
  assert.deepEqual(state.imageSessionEntries.map(e => e.taskId), ['task-old','task-new']);
  assert.deepEqual(state.imageSessionEntries[1].media.map(m => m.media_id), ['new-2','new-1']);
  assert.deepEqual(renders.at(-1), ['old-1','new-2','new-1']);
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
"""
    result = subprocess.run(
        ["node", "-e", harness],
        input=script.text.encode("utf-8"),
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_v34_canvas_content_url_is_promoted_to_authenticated_thumbnail() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    gateway = client.get("/web-assets/saas-canvas-gateway.js")
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
let hydrated = null;
let thumbs = 0;
const canvas = {canvas_id:'canvas-v34', title:'V34', kind:'smart', version:1,
  created_at:'2026-08-20T00:00:00Z', updated_at:'2026-08-20T00:00:00Z',
  document:{nodes:[{id:'n1', type:'smart-image', images:[{url:'/api/v1/media/media-legacy/content', kind:'image'}]}], connections:[]}};
const json = value => new Response(JSON.stringify(value), {status:200, headers:{'Content-Type':'application/json'}});
async function nativeFetch(input) {
  const path = String(input);
  if (path === '/api/v1/canvases/canvas-v34') return json(canvas);
  if (path === '/api/v1/canvases/canvas-v34/generation-tasks/recent?limit=20') return json([]);
  if (path === '/api/v1/canvases/canvas-v34/generation-tasks/active') return json([]);
  if (path === '/api/v1/media/media-legacy/thumbnail?size=512') {
    thumbs += 1;
    return new Response(new Uint8Array([1,2,3]), {status:200, headers:{'Content-Type':'image/webp'}});
  }
  throw new Error(`unexpected request: ${path}`);
}
global.window = {
  location:{pathname:'/workspace/canvases/canvas-v34/smart', origin:'http://test', replace(){}},
  sessionStorage:{getItem(key){return key === 'creative_studio_access_token' ? 'token' : null;}, setItem(){}, removeItem(){}},
  fetch:nativeFetch, setTimeout, clearTimeout,
  addEventListener(){},
  dispatchEvent(event){if(event.type === 'saas-canvas-hydrated') hydrated = event.detail.canvas;},
  CustomEvent:class CustomEvent {constructor(type, init){this.type=type;this.detail=init.detail;}},
};
global.document = {getElementById(){return null;}, body:{appendChild(){}}, createElement(){return {};}};
URL.createObjectURL = () => 'blob:thumb'; URL.revokeObjectURL = () => {};
(async () => {
  eval(source);
  const response = await window.fetch('/api/canvases/canvas-v34');
  const initial = (await response.json()).canvas;
  assert.equal(initial.nodes[0].images[0].media_id, 'media-legacy');
  assert.match(initial.nodes[0].images[0].thumbnail, /^data:image\/gif/);
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(thumbs, 1);
  assert.equal(hydrated.nodes[0].images[0].thumbnail, 'blob:thumb');
  assert.equal(hydrated.nodes[0].images[0].url, '/api/v1/media/media-legacy/content');
})().catch(error => {console.error(error.stack || error); process.exitCode = 1;});
"""
    result = subprocess.run(["node", "-e", harness], input=gateway.text, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr


def test_v34_canvas_thumbnails_render_progressively_with_four_requests_max() -> None:
    client = TestClient(create_app(InMemoryAccountAccess()))
    gateway = client.get("/web-assets/saas-canvas-gateway.js")
    harness = r"""
const assert = require('node:assert/strict');
const source = require('node:fs').readFileSync(0, 'utf8');
const imageCount = 6;
let active = 0;
let peak = 0;
const hydratedCounts = [];
const images = Array.from({length:imageCount}, (_, index) => ({
  media_id:`media-${index + 1}`, kind:'image',
  url:`/api/v1/media/media-${index + 1}/content`,
}));
const canvas = {
  canvas_id:'canvas-progressive-v34', title:'Progressive', kind:'smart', version:1,
  created_at:'2026-08-20T00:00:00Z', updated_at:'2026-08-20T00:00:00Z',
  document:{nodes:[{id:'n1', type:'smart-image', images}], connections:[]},
};
const json = value => new Response(JSON.stringify(value), {
  status:200, headers:{'Content-Type':'application/json'},
});
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
async function nativeFetch(input) {
  const path = String(input);
  if (path === '/api/v1/canvases/canvas-progressive-v34') return json(canvas);
  if (path.includes('/generation-tasks/')) return json([]);
  if (path.includes('/thumbnail?size=512')) {
    active += 1;
    peak = Math.max(peak, active);
    await wait(3);
    active -= 1;
    return new Response(new Uint8Array([1,2,3]), {
      status:200, headers:{'Content-Type':'image/webp'},
    });
  }
  throw new Error(`unexpected request: ${path}`);
}
global.window = {
  location:{pathname:'/workspace/canvases/canvas-progressive-v34/smart', origin:'http://test', replace(){}},
  sessionStorage:{getItem(){return 'token';}, setItem(){}, removeItem(){}},
  fetch:nativeFetch, setTimeout, clearTimeout,
  addEventListener(){},
  dispatchEvent(event){
    if (event.type !== 'saas-canvas-hydrated') return;
    const current = event.detail.canvas.nodes[0].images;
    hydratedCounts.push(current.filter(item => String(item.thumbnail || '').startsWith('blob:')).length);
  },
  CustomEvent:class CustomEvent {
    constructor(type, init){this.type=type; this.detail=init.detail;}
  },
};
global.document = {getElementById(){return null;}, body:{appendChild(){}}, createElement(){return {};}};
let blobIndex = 0;
URL.createObjectURL = () => `blob:thumb-${++blobIndex}`;
URL.revokeObjectURL = () => {};
(async () => {
  eval(source);
  const response = await window.fetch('/api/canvases/canvas-progressive-v34');
  const initial = (await response.json()).canvas;
  assert.equal(initial.nodes[0].images.filter(item => String(item.thumbnail || '').startsWith('data:')).length, imageCount);
  await new Promise(resolve => setTimeout(resolve, 30));
  assert.ok(peak <= 4, `thumbnail concurrency exceeded four: ${peak}`);
  assert.ok(hydratedCounts.length >= imageCount, `expected progressive events: ${hydratedCounts}`);
  assert.ok(hydratedCounts.slice(0, imageCount).every((count, index) => count >= index + 1));
})().catch(error => {console.error(error.stack || error); process.exitCode = 1;});
"""
    result = subprocess.run(
        ["node", "-e", harness],
        input=gateway.text.encode("utf-8"),
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
