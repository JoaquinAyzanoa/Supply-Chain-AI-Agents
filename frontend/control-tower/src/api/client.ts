/**
 * The typed API client, generated from the director's OpenAPI document
 * (`npm run openapi` -> `schema.d.ts`). Every request carries the session
 * token; a 401 signs the user out so the login page shows instead of a
 * broken screen.
 */
import createClient, { type Middleware } from "openapi-fetch";

import type { components, paths } from "./schema";
import { authStore } from "@/auth/store";

export type Schemas = components["schemas"];

// Same origin as the page: the director serves the bundle and the API together
// (in development Vite proxies /api). An absolute base keeps `Request` happy in tests.
export const api = createClient<paths>({
  baseUrl: window.location.origin,
  fetch: (request) => globalThis.fetch(request), // resolved per call, so tests can stub fetch
});

const auth: Middleware = {
  onRequest({ request }) {
    const token = authStore.getToken();
    if (token) request.headers.set("Authorization", `Bearer ${token}`);
    return request;
  },
  onResponse({ response }) {
    if (response.status === 401 && authStore.getToken()) authStore.clear();
    return response;
  },
};

api.use(auth);

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: unknown,
  ) {
    super(typeof detail === "string" ? detail : `HTTP ${status}`);
  }
}

/** Unwrap an openapi-fetch result: data, or throw an `ApiError` with the server's detail. */
export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.error !== undefined || result.data === undefined) {
    const detail =
      result.error && typeof result.error === "object" && "detail" in result.error
        ? (result.error as { detail: unknown }).detail
        : result.error;
    throw new ApiError(result.response.status, detail);
  }
  return result.data;
}
