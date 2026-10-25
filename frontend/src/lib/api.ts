/**
 * Shared JSON fetch helper. Throws on non-2xx responses with the error message
 * from the response body when available.
 *
 * @param path Request path
 * @param options Fetch options
 */
export class ApiResponseError extends Error {
  status: number;
  payload: unknown;

  constructor(message: string, status: number, payload: unknown = {}) {
    super(message);
    this.name = "ApiResponseError";
    this.status = status;
    this.payload = payload;
  }
}

export async function fetchJson<T = unknown>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, options);
  const text = await response.text();
  let data: unknown = {};
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      if (!response.ok) throw new Error(`HTTP ${response.status}: ${text}`);
      throw new Error(`Invalid JSON from ${path}`);
    }
  }
  if (!response.ok) {
    const errObj = data && typeof data === "object" ? (data as Record<string, unknown>) : null;
    // The workbench's own routes answer {"error": ...}; the runtime answers
    // {"status": {"code", "message"}} (#580), so its reason is read too.
    const status = errObj?.status && typeof errObj.status === "object" ? errObj.status : null;
    const runtimeMessage = (status as Record<string, unknown> | null)?.message;
    const message =
      typeof errObj?.error === "string" && errObj.error.trim() !== ""
        ? errObj.error
        : typeof runtimeMessage === "string" && runtimeMessage.trim() !== ""
          ? runtimeMessage
          : `HTTP ${response.status}`;
    throw new ApiResponseError(message, response.status, data);
  }
  return data as T;
}

/**
 * Shared raw fetch helper for non-JSON payloads (downloads, health probes).
 *
 * @param path Request path or URL
 * @param options Fetch options
 */
export async function fetchResponse(path: string, options: RequestInit = {}): Promise<Response> {
  return fetch(path, options);
}

/**
 * Download a Blob as a file attachment.
 *
 * @param blob Blob payload
 * @param filename Download name
 */
export function downloadBlob(blob: Blob, filename: string): void {
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
}

/**
 * Parse a Content-Disposition header value to extract a filename.
 *
 * @param headerValue Content-Disposition header value
 * @param fallback Fallback filename
 */
export function filenameFromContentDisposition(
  headerValue: string | null,
  fallback: string,
): string {
  if (typeof headerValue !== "string" || headerValue.trim() === "") return fallback;

  const utf8Match = headerValue.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match?.[1]) {
    try {
      return decodeURIComponent(utf8Match[1]);
    } catch {
      return utf8Match[1];
    }
  }

  const simpleMatch = headerValue.match(/filename="?([^\";]+)"?/i);
  if (simpleMatch?.[1]) return simpleMatch[1];

  return fallback;
}
