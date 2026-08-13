// Empty by default so production builds call the Worker that served this page.
// Local development points it at the separate Worker port via .env.
export const API_URL = import.meta.env.VITE_API_URL ?? "";

export async function api<T>(path: string, token: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}), ...init.headers },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: "Request failed." }));
    throw new Error(payload.detail ?? "Request failed.");
  }
  return response.status === 204 ? (undefined as T) : response.json();
}
