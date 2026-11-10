export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function readErrorMessage(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body.detail === 'string') return body.detail;
    if (Array.isArray(body.detail) && body.detail[0]?.msg) {
      const field = body.detail[0].loc?.at(-1);
      return field ? `${field}: ${body.detail[0].msg}` : body.detail[0].msg;
    }
  } catch {
    // Non-JSON error bodies (e.g. from nginx) fall through to the status message below.
  }
  if (response.status === 429) return 'Too many attempts. Wait a moment and try again.';
  return `Request failed (${response.status})`;
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const isJson = typeof init?.body === 'string';
  const response = await fetch(path, {
    ...init,
    credentials: 'same-origin',
    headers: isJson ? { 'Content-Type': 'application/json' } : undefined,
  });
  if (!response.ok) throw new ApiError(response.status, await readErrorMessage(response));
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
