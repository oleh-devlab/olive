export class ApiError extends Error {
  status: number;

  constructor(message: string, status = 0) {
    super(message);
    this.status = status;
  }
}

export async function api<T = unknown>(method: string, path: string, body?: unknown): Promise<T> {
  const options: RequestInit = { method, headers: {}, credentials: "same-origin" };
  if (body !== undefined) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(body);
  }

  let response: Response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new ApiError("The server is not responding.");
  }

  if (response.status === 204) return null as T;

  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(data.error || `Error ${response.status}`, response.status);
  return data as T;
}
