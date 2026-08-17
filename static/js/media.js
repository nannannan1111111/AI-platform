import {requestJson as defaultRequestJson} from './http-client.js?v=2026.08.07.1';

const LOCAL_MEDIA_PREFIXES = ['/output/', '/assets/', '/api/media/'];
const PREVIEWABLE_MEDIA = /\.(png|jpe?g|webp|gif|bmp|avif|tiff?|mp4|webm|mov|m4v|avi|mkv|flv)(\?|#|$)/i;

function mediaUrlValue(itemOrUrl) {
    return typeof itemOrUrl === 'string' ? itemOrUrl : itemOrUrl?.url || '';
}

function isLocalMediaUrl(url) {
    return LOCAL_MEDIA_PREFIXES.some(prefix => url.startsWith(prefix));
}

function safeBrowserMediaUrl(url) {
    const raw = String(url || '').trim();
    if (!raw || raw.startsWith('//')) return '';
    if (/^https?:\/\//i.test(raw) || /^blob:/i.test(raw)) return raw;
    if (/^data:(?:image|video|audio)\//i.test(raw)) return raw;
    if (raw.startsWith('/') || raw.startsWith('./') || raw.startsWith('../')) return raw;
    return '';
}

/** 返回媒体预览地址中隐藏的原始 URL。 */
export function originalMediaUrl(itemOrUrl) {
    const raw = String(mediaUrlValue(itemOrUrl) || '');
    if (!raw) return '';
    try {
        const baseUrl = globalThis.location?.origin || 'http://localhost';
        const parsed = new URL(raw, baseUrl);
        if (parsed.pathname === '/api/media-preview') {
            return parsed.searchParams.get('url') || raw;
        }
    } catch (_) {}
    return raw;
}

/** 从媒体 URL 提取下载文件名，不改变原地址。 */
export function mediaFileName(url = '') {
    const raw = String(url || '');
    try {
        const baseUrl = globalThis.location?.href || 'http://localhost/';
        const parsed = new URL(raw, baseUrl);
        return decodeURIComponent(parsed.pathname.split('/').filter(Boolean).pop() || '');
    } catch (_) {
        return decodeURIComponent(raw.split('?')[0].split('#')[0].split('/').filter(Boolean).pop() || '');
    }
}

/** 让远程媒体通过画布既有的同源下载端点展示。 */
export function proxiedMediaUrl(itemOrUrl, name = '', {proxyUnknown = false} = {}) {
    const raw = originalMediaUrl(itemOrUrl);
    if (!raw || isLocalMediaUrl(raw) || raw.startsWith('data:') || raw.startsWith('blob:')) return raw;
    if (!proxyUnknown && !/^https?:\/\//i.test(raw)) return raw;
    const itemName = typeof itemOrUrl === 'object' && itemOrUrl ? itemOrUrl.name || '' : '';
    const filename = name || itemName || mediaFileName(raw) || 'preview';
    return `/api/download-output?inline=1&url=${encodeURIComponent(raw)}&name=${encodeURIComponent(filename)}`;
}

/** 返回浏览器可展示的 URL，本地媒体仍使用原路径。 */
export function displayMediaUrl(itemOrUrl, name = '') {
    const raw = originalMediaUrl(itemOrUrl);
    const displayUrl = /^https?:\/\//i.test(raw) ? proxiedMediaUrl(itemOrUrl, name) : raw;
    return safeBrowserMediaUrl(displayUrl);
}

/** 为支持的本地图片和视频生成限制尺寸的同源预览地址。 */
export function mediaPreviewUrl(itemOrUrl, size = 512, {useAbsolutePath = false} = {}) {
    const raw = originalMediaUrl(itemOrUrl);
    const displayUrl = displayMediaUrl(
        typeof itemOrUrl === 'object' && itemOrUrl ? {...itemOrUrl, url: raw} : raw,
    );
    if (!raw || raw.startsWith('data:') || raw.startsWith('blob:')) return displayUrl;

    let previewSource = raw;
    if (/^https?:\/\//i.test(raw)) {
        if (!useAbsolutePath) return displayUrl;
        try {
            previewSource = new URL(raw).pathname;
        } catch (_) {
            return displayUrl;
        }
    }
    if (!isLocalMediaUrl(previewSource) || !PREVIEWABLE_MEDIA.test(previewSource)) return displayUrl;
    const width = Math.max(64, Math.min(2048, Math.round(Number(size) || 512)));
    return `/api/media-preview?w=${width}&url=${encodeURIComponent(previewSource)}`;
}

/** 检查缺失和过期媒体，不修改页面持有的状态。 */
export async function checkMediaAvailability(urls, {requestJson = defaultRequestJson} = {}) {
    const requested = [...new Set((urls || []).map(url => String(url || '')).filter(Boolean))];
    if (!requested.length) return {exists: {}, states: {}, missing: new Set(), expired: new Set()};
    const data = (await requestJson('/api/canvas-assets/check', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({urls: requested}),
    })) || {};
    const exists = data.exists && typeof data.exists === 'object' ? data.exists : {};
    const states = data.states && typeof data.states === 'object' ? data.states : {};
    const missing = new Set(requested.filter(url => exists[url] === false));
    const expired = new Set(requested.filter(url => states[url] === 'expired'));
    return {exists, states, missing, expired};
}

/** 保留受管临时媒体；旧式 URL 原样返回。 */
export async function promoteMedia(
    itemOrUrl,
    {ownerType = 'canvas', ownerId = '', requestJson = defaultRequestJson} = {},
) {
    const url = originalMediaUrl(itemOrUrl);
    const clean = url.split('?', 1)[0];
    if (!clean.startsWith('/api/media/')) return {url, state: 'legacy'};
    const hasOwner = Boolean(ownerType && ownerId);
    return requestJson(`${clean}/promote`, {
        method: 'POST',
        headers: hasOwner ? {'Content-Type': 'application/json'} : {},
        body: hasOwner ? JSON.stringify({owner_type: ownerType, owner_id: ownerId}) : undefined,
    });
}

/** 返回两种画布共享的缺失或过期展示信息。 */
export function unavailableMediaPresentation({expired = false, language = 'zh'} = {}) {
    const english = String(language || '').toLowerCase().startsWith('en');
    if (expired) {
        return {
            kind: 'expired',
            icon: 'clock-3',
            message: english ? 'Result expired · regenerate' : '结果已过期，可重新生成',
        };
    }
    return {
        kind: 'missing',
        icon: 'image-off',
        message: english ? 'Missing file' : '文件缺失',
    };
}
