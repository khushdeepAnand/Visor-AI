// Typed request helpers over the generated OpenAPI route table. Every call is
// checked against the backend schema at compile time: a renamed path, changed
// body or re-typed response becomes a TypeScript build error.
import { api, ApiError, API_BASE } from "./api";
import type { RouteBody, RouteKey, RouteParams, RouteQuery, RouteResponse } from "./generated/routes";
import { ROUTE_KEYS, ROUTE_COUNT } from "./generated/routes";

/** Serialize a query object, skipping undefined/null values. */
function serializeQuery(query: object | never | undefined): string {
  if (!query) return "";
  const entries = Object.entries(query as Record<string, unknown>).filter(
    ([, value]) => value !== undefined && value !== null,
  );
  if (!entries.length) return "";
  return (
    "?" +
    entries
      .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`)
      .join("&")
  );
}

/** Fill `{param}` placeholders from the route's params object. */
function buildPath<K extends RouteKey>(path: string, params: RouteParams<K>): string {
  return path.replace(/\{([^}]+)\}/g, (_match, name: string) => {
    const value = (params as Record<string, unknown>)[name];
    if (value === undefined || value === null) {
      throw new Error(`Missing path parameter "${name}" for route ${path}.`);
    }
    return encodeURIComponent(String(value));
  });
}

/**
 * Perform a typed request. Type parameters are inferred from the route key:
 *
 *   await typedApi<"post /api/v1/auth/login">("/api/v1/auth/login", { body });
 */
export async function typedApi<K extends RouteKey>(
  key: K,
  options: {
    params?: RouteParams<K>;
    query?: RouteQuery<K>;
    body?: RouteBody<K>;
    init?: RequestInit;
  } = {},
): Promise<RouteResponse<K>> {
  const path = buildPath<K>(key.split(" ")[1], (options.params ?? {}) as RouteParams<K>);
  const url = `${path}${serializeQuery(options.query as object | never | undefined)}`;
  const init: RequestInit = { ...options.init };
  if (options.body !== undefined && options.body !== null) {
    init.method = init.method ?? key.split(" ")[0].toUpperCase();
    init.body = JSON.stringify(options.body);
  }
  return api<RouteResponse<K>>(url, init);
}

/** Same as typedApi but infers everything from one options object. */
export function route<K extends RouteKey>(
  key: K,
  options: {
    params?: RouteParams<K>;
    query?: RouteQuery<K>;
    body?: RouteBody<K>;
    init?: RequestInit;
  } = {},
): Promise<RouteResponse<K>> {
  return typedApi(key, options);
}

/** Every generated route key — used by drift/exhaustiveness tests. */
export const allRouteKeys = ROUTE_KEYS;
export const routeCount = ROUTE_COUNT;

export { ApiError };
