// Reads the shared fixtures without losing what JavaScript numbers cannot hold: `1` and `1.0` are
// different JSON numbers and the canonical text depends on it (schema/corpus/README.md). Every
// number is kept as the text it was written with, and `toText` writes that text back out.
class Raw {
  constructor(source) {
    this.source = source;
  }
}

/** Whether this engine passes the source text of each primitive to the reviver (V8 11.4+: Node 21+, Chrome 114+, Deno, Workers). */
export const LOSSLESS = (() => {
  let seen;
  JSON.parse("1.0", (_key, value, context) => {
    seen = context?.source;
    return value;
  });
  return seen === "1.0";
})();

export function parseLossless(text) {
  return JSON.parse(text, (_key, value, context) =>
    typeof value === "number" ? new Raw(context.source) : value,
  );
}

export function toText(value) {
  if (value instanceof Raw) return value.source;
  if (Array.isArray(value)) return `[${value.map(toText).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.entries(value)
      .map(([key, item]) => `${JSON.stringify(key)}:${toText(item)}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

/** The same value with every number as a plain JavaScript number, for comparing parsed results. */
export function plain(value) {
  return JSON.parse(toText(value));
}
