#!/usr/bin/env node
/**
 * Generates frontend/lib/generated/routes.ts from openapi.json.
 *
 * The generated file is the typed API client core: every backend operation
 * becomes a literal route key with typed path params, query, request body and
 * response. Regenerate after backend changes:
 *
 *   python scripts/export_openapi.py
 *   npm run generate:api
 *
 * With --check the script exits non-zero when the committed client is stale,
 * which makes API drift a build error (`prebuild` runs it).
 */
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const SPEC_PATH = join(root, "lib", "generated", "openapi.json");
const OUT_PATH = join(root, "lib", "generated", "routes.ts");

const check = process.argv.includes("--check");
const spec = JSON.parse(readFileSync(SPEC_PATH, "utf8"));

// ---------------------------------------------------------------------------
// JSON Schema -> TypeScript
// ---------------------------------------------------------------------------

function refName(ref) {
  const name = ref.split("/").pop();
  return name || "unknown";
}

function tsType(schema, depth = 0) {
  if (!schema || typeof schema !== "object" || depth > 12) return "unknown";
  if (schema.$ref) return refName(schema.$ref);
  if (Array.isArray(schema.allOf)) {
    const parts = schema.allOf.map((s) => tsType(s, depth + 1)).filter((t) => t !== "unknown");
    return parts.length ? parts.join(" & ") : "unknown";
  }
  const union = schema.oneOf || schema.anyOf;
  if (Array.isArray(union) && union.length) {
    const parts = [...new Set(union.map((s) => tsType(s, depth + 1)))];
    return parts.length === 1 ? parts[0] : `(${parts.join(" | ")})`;
  }
  if (Array.isArray(schema.enum) && schema.enum.length) {
    return schema.enum.map((v) => JSON.stringify(v)).join(" | ");
  }
  if (schema.const !== undefined) return JSON.stringify(schema.const);

  const type = schema.type;
  if (Array.isArray(type)) {
    const nonNull = type.filter((t) => t !== "null");
    const base = nonNull.length ? tsType({ ...schema, type: nonNull[0] }, depth + 1) : "unknown";
    return type.includes("null") ? `${base} | null` : base;
  }

  switch (type) {
    case "string":
      return "string";
    case "integer":
    case "number":
      return "number";
    case "boolean":
      return "boolean";
    case "null":
      return "null";
    case "array": {
      const item = tsType(schema.items, depth + 1);
      const needsParens = item.includes(" ") && !item.startsWith("(");
      return needsParens ? `Array<${item}>` : `${item}[]`;
    }
    case "object":
    case undefined: {
      if (schema.properties && Object.keys(schema.properties).length) {
        const required = new Set(schema.required || []);
        const fields = Object.keys(schema.properties)
          .sort()
          .map((key) => {
            const optional = required.has(key) ? "" : "?";
            const safeKey = /^[A-Za-z_$][A-Za-z0-9_$]*$/.test(key) ? key : JSON.stringify(key);
            return `${safeKey}${optional}: ${tsType(schema.properties[key], depth + 1)}`;
          });
        return `{ ${fields.join("; ")} }`;
      }
      if (schema.additionalProperties && schema.additionalProperties !== false) {
        if (schema.additionalProperties === true) return "Record<string, unknown>";
        return `Record<string, ${tsType(schema.additionalProperties, depth + 1)}>`;
      }
      if (type === "object") return "Record<string, unknown>";
      return "unknown";
    }
    default:
      return "unknown";
  }
}

function tsDoc(summary, description) {
  const text = [summary, description].filter(Boolean).join(" — ").replace(/\*\//g, "* /");
  if (!text) return "";
  return `  /** ${text} */\n`;
}

function pathParams(path) {
  const params = [...path.matchAll(/\{([^}]+)\}/g)].map((m) => m[1]);
  return params;
}

function responseType(op) {
  const ok = op.responses?.["200"] || op.responses?.["201"] || op.responses?.["default"];
  const schema = ok?.content?.["application/json"]?.schema;
  return tsType(schema, 4);
}

function queryType(op) {
  const params = (op.parameters || []).filter((p) => p.in === "query");
  if (!params.length) return "never";
  const required = new Set((op.parameters || []).filter((p) => p.required).map((p) => p.name));
  const fields = params
    .sort((a, b) => a.name.localeCompare(b.name))
    .map((p) => {
      const safe = /^[A-Za-z_$][A-Za-z0-9_$]*$/.test(p.name) ? p.name : JSON.stringify(p.name);
      const opt = required.has(p.name) ? "" : "?";
      const t = p.schema ? tsType(p.schema, 6) : "unknown";
      return `${safe}${opt}: ${t}`;
    });
  return `{ ${fields.join("; ")} }`;
}

function bodyType(op) {
  const body = op.requestBody?.content?.["application/json"]?.schema;
  if (!body) return "never";
  const required = op.requestBody.required !== false;
  return { text: tsType(body, 4), required };
}

// ---------------------------------------------------------------------------
// Build the route table
// ---------------------------------------------------------------------------

const METHODS = ["get", "post", "put", "patch", "delete"];
const operations = [];

for (const path of Object.keys(spec.paths || {}).sort()) {
  const item = spec.paths[path];
  for (const method of METHODS) {
    const op = item[method];
    if (!op) continue;
    const p = pathParams(path);
    const body = bodyType(op);
    operations.push({
      key: `${method} ${path}`,
      method,
      path,
      op,
      params: p,
      query: queryType(op),
      body,
      response: responseType(op),
    });
  }
}

// Named component schemas -> TS type aliases (sorted for determinism).
const schemas = spec.components?.schemas || {};
function resolveRoot(schema, depth = 0) {
  let current = schema;
  while (current && current.$ref && depth < 10) {
    const name = refName(current.$ref);
    current = schemas[name];
    depth += 1;
  }
  return current || {};
}
const schemaNames = Object.keys(schemas).sort();
const schemaTypes = schemaNames
  .map((name) => `export type ${name} = ${tsType(resolveRoot(schemas[name]), 0)};`)
  .join("\n");

const routeKeys = operations.map((o) => `  | ${JSON.stringify(o.key)}`).join("\n");

const routeEntries = operations
  .map((o) => {
    const paramType = o.params.length
      ? `{ ${o.params.map((n) => `${n}: string | number`).join("; ")} }`
      : "never";
    const doc = tsDoc(o.op.summary, o.op.description);
    const body = o.body.text === "never" ? "never" : o.body.text;
    return (
      doc +
      `  ${JSON.stringify(o.key)}: {\n` +
      `    method: ${JSON.stringify(o.method)};\n` +
      `    path: ${JSON.stringify(o.path)};\n` +
      `    params: ${paramType};\n` +
      `    query: ${o.query};\n` +
      `    body: ${body};\n` +
      `    response: ${o.response};\n` +
      `  };`
    );
  })
  .join("\n");

const template = `/* eslint-disable */
/**
 * AUTO-GENERATED by frontend/scripts/generate-api-client.mjs — DO NOT EDIT.
 * Source: lib/generated/openapi.json (backend FastAPI schema).
 *
 * Regenerate:
 *   python scripts/export_openapi.py && npm run generate:api
 *
 * Drift check (runs on \`npm run build\` via prebuild):
 *   npm run check:api
 */

export type HttpMethod = ${METHODS.map((m) => JSON.stringify(m)).join(" | ")};

/** Every backend operation as a literal "\${method} \${path}" key. */
export type RouteKey =
${routeKeys};

/** Typed description of one route: params, query, body and response. */
export type RouteMap = {
${routeEntries}
};

export type RouteMethod<K extends RouteKey> = RouteMap[K]["method"];
export type RoutePath<K extends RouteKey> = RouteMap[K]["path"];
export type RouteParams<K extends RouteKey> = RouteMap[K]["params"];
export type RouteQuery<K extends RouteKey> = RouteMap[K]["query"];
export type RouteBody<K extends RouteKey> = RouteMap[K]["body"];
export type RouteResponse<K extends RouteKey> = RouteMap[K]["response"];

export type OperationId = ${operations.map((o) => JSON.stringify(o.op.operationId || o.key)).join(" | ") || "never"};

// --- Component schemas -------------------------------------------------------
${schemaTypes}

/** Number of operations captured from the OpenAPI schema. */
export const ROUTE_COUNT = ${operations.length};

/** Sorted route keys for exhaustive iteration in tests. */
export const ROUTE_KEYS: readonly RouteKey[] = [
${operations.map((o) => `  ${JSON.stringify(o.key)},`).join("\n")}
];
`;

const finalText = template;

if (check) {
  if (!existsSync(OUT_PATH)) {
    console.error(`MISSING: lib/generated/routes.ts (run \`npm run generate:api\`)`);
    process.exit(1);
  }
  const current = readFileSync(OUT_PATH, "utf8");
  if (current !== finalText) {
    console.error(
      "API client drift: lib/generated/routes.ts is stale.\n" +
        "Run: python scripts/export_openapi.py && npm run generate:api",
    );
    process.exit(1);
  }
  console.log(`API client in sync (${operations.length} routes, ${schemaNames.length} schemas).`);
} else {
  writeFileSync(OUT_PATH, finalText, "utf8");
  console.log(`Wrote lib/generated/routes.ts (${operations.length} routes, ${schemaNames.length} schemas).`);
}
