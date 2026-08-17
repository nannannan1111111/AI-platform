const REQUEST_ID_HEADER = 'x-request-id';
const DEFAULT_ERROR_MESSAGE = '操作失败';

function defaultRequestIdFactory() {
    if (globalThis.crypto?.randomUUID) {
        return globalThis.crypto.randomUUID().replaceAll('-', '');
    }
    if (globalThis.crypto?.getRandomValues) {
        const bytes = new Uint8Array(16);
        globalThis.crypto.getRandomValues(bytes);
        return Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
    }
    const timestamp = Date.now().toString(16).padStart(12, '0');
    const random = Math.floor(Math.random() * Number.MAX_SAFE_INTEGER).toString(16).padStart(20, '0');
    return `${timestamp}${random}`.slice(0, 32);
}

function requestHeaders(input, init, requestIdFactory) {
    const headers = new Headers(input instanceof Request ? input.headers : undefined);
    new Headers(init.headers).forEach((value, name) => headers.set(name, value));
    if (!headers.has(REQUEST_ID_HEADER)) {
        headers.set(REQUEST_ID_HEADER, String(requestIdFactory() || defaultRequestIdFactory()));
    }
    return headers;
}

async function responseBody(response) {
    const text = await response.text();
    if (!text) return {data: null, isJson: true};
    try {
        return {data: JSON.parse(text), isJson: true};
    } catch (_) {
        return {data: text, isJson: false};
    }
}

function errorMessage(data, fallback = DEFAULT_ERROR_MESSAGE) {
    if (typeof data === 'string' && data.trim()) return data;
    if (!data || typeof data !== 'object') return fallback;
    if (typeof data.detail === 'string' && data.detail.trim()) return data.detail;
    if (typeof data.message === 'string' && data.message.trim()) return data.message;
    if (typeof data.error === 'string' && data.error.trim()) return data.error;
    return fallback;
}

/** 表示一次同源 HTTP 请求的稳定错误结构。 */
export class HttpError extends Error {
    constructor(message, {status = 0, requestId = '', data = null, cause} = {}) {
        super(message, cause ? {cause} : undefined);
        this.name = 'HttpError';
        this.status = status;
        this.requestId = requestId;
        this.data = data;
    }
}

/**
 * 创建 HTTP 客户端；测试可注入 fetch fake，生产默认使用浏览器 fetch。
 * 每次请求都会携带可追踪的 x-request-id，失败时抛出 HttpError。
 */
export function createHttpClient({fetchImpl = globalThis.fetch?.bind(globalThis), requestIdFactory = defaultRequestIdFactory} = {}) {
    if (typeof fetchImpl !== 'function') throw new TypeError('fetchImpl 必须是函数');
    if (typeof requestIdFactory !== 'function') throw new TypeError('requestIdFactory 必须是函数');

    const responseRequestIds = new WeakMap();

    function getRequestId(response) {
        return responseRequestIds.get(response) || response?.headers?.get?.(REQUEST_ID_HEADER) || '';
    }

    async function request(input, init = {}) {
        const headers = requestHeaders(input, init, requestIdFactory);
        const clientRequestId = headers.get(REQUEST_ID_HEADER) || '';
        let response;
        try {
            response = await fetchImpl(input, {...init, headers});
        } catch (cause) {
            throw new HttpError(cause?.message || '网络请求失败', {
                requestId: clientRequestId,
                cause,
            });
        }

        const requestId = response.headers?.get?.(REQUEST_ID_HEADER) || clientRequestId;
        responseRequestIds.set(response, requestId);
        if (!response.ok) {
            const {data, isJson} = await responseBody(response);
            throw new HttpError(errorMessage(isJson ? data : null), {
                status: response.status,
                requestId,
                data,
            });
        }
        return response;
    }

    async function requestJson(input, init = {}) {
        const response = await request(input, init);
        const {data, isJson} = await responseBody(response);
        if (isJson) return data;
        throw new HttpError('响应不是有效 JSON', {
            status: response.status,
            requestId: getRequestId(response),
            data,
        });
    }

    return Object.freeze({request, requestJson, getRequestId});
}

const defaultClient = createHttpClient();

export const request = defaultClient.request;
export const requestJson = defaultClient.requestJson;
export const getRequestId = defaultClient.getRequestId;
