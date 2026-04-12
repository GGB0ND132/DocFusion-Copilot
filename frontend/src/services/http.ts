const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000').replace(/\/$/, '');

export class ApiError extends Error {
  status: number;
  detail?: unknown;

  constructor(message: string, status: number, detail?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

type RequestOptions = Omit<RequestInit, 'body'> & {
  body?: BodyInit | null;
};

export function buildApiUrl(path: string): string {
  if (/^https?:\/\//.test(path)) {
    return path;
  }
  return `${API_BASE_URL}${path.startsWith('/') ? path : `/${path}`}`;
}

async function parseError(response: Response): Promise<never> {
  let detail: unknown;

  try {
    detail = await response.json();
  } catch {
    detail = await response.text();
  }

  const message =
    typeof detail === 'object' && detail !== null && 'detail' in detail
      ? String((detail as { detail: unknown }).detail)
      : `Request failed with status ${response.status}`;

  throw new ApiError(message, response.status, detail);
}

const _etagCache = new Map<string, { etag: string; data: unknown }>();

export async function requestJson<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const url = buildApiUrl(path);
  const method = (options.method ?? 'GET').toUpperCase();

  // Attach cached ETag for GET requests
  const headers: Record<string, string> = {};
  if (method === 'GET') {
    const cached = _etagCache.get(url);
    if (cached) {
      headers['If-None-Match'] = cached.etag;
    }
  }

  const response = await fetch(url, {
    ...options,
    headers: { ...headers, ...(options.headers as Record<string, string> | undefined) },
  });

  // 304 Not Modified — return cached data
  if (response.status === 304) {
    const cached = _etagCache.get(url);
    if (cached) return cached.data as T;
  }

  if (!response.ok) {
    return parseError(response);
  }

  const data = (await response.json()) as T;

  // Cache ETag for GET responses
  if (method === 'GET') {
    const etag = response.headers.get('etag');
    if (etag) {
      _etagCache.set(url, { etag, data });
    }
  }

  return data;
}

export async function requestFile(path: string, options: RequestOptions = {}): Promise<{ blob: Blob; fileName: string }> {
  const response = await fetch(buildApiUrl(path), options);

  if (!response.ok) {
    return parseError(response);
  }

  const disposition = response.headers.get('content-disposition') ?? '';
  const matchedFileName = disposition.match(/filename\*=UTF-8''([^;]+)|filename="?([^";]+)"?/i);
  const contentType = (response.headers.get('content-type') ?? '').toLowerCase();
  const defaultExtension = contentType.includes('wordprocessingml.document')
    ? '.docx'
    : contentType.includes('spreadsheetml.sheet')
      ? '.xlsx'
      : '.bin';
  const fileName = decodeURIComponent(
    matchedFileName?.[1] ?? matchedFileName?.[2] ?? `download${defaultExtension}`,
  );

  return {
    blob: await response.blob(),
    fileName,
  };
}

export { API_BASE_URL };
