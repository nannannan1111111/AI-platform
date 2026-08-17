import * as media from './media.js?v=2026.08.07.1';

const entryUrl = new URL(import.meta.url);
const entry = entryUrl.searchParams.get('entry') || '';
const entryVersion = entryUrl.searchParams.get('entryVersion') || '';
if (!['canvas.js', 'smart-canvas.js'].includes(entry)) {
    throw new Error('Unsupported legacy media entry');
}

// The legacy editors still expose global functions, while their static controls are bound
// through csp-event-bridge.js so the pages do not require inline script handlers.
globalThis.StudioMedia = Object.freeze({...media});
const script = document.createElement('script');
script.async = false;
script.src = `/static/js/${entry}${entryVersion ? `?v=${encodeURIComponent(entryVersion)}` : ''}`;
await new Promise((resolve, reject) => {
    script.addEventListener('load', resolve, {once: true});
    script.addEventListener('error', () => reject(new Error(`Failed to load ${entry}`)), {once: true});
    document.body.append(script);
});
