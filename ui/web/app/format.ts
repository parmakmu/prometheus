// Numbers as the page shows them

// a cost in dollars: $0 (no model ran), $0.0042, $1.20; nothing when unknown
export const money = (n?: number | null) => (n == null ? "" : n === 0 ? "$0" : `$${n < 0.01 ? n.toFixed(4) : n.toFixed(2)}`);

// a count of tokens or a context window: 812, 1.2k, 200k, 1M
export const kilo = (n?: number | null) => {
  if (!n) return "";
  const [d, unit] = n >= 1e6 ? [1e6, "M"] : n >= 1e3 ? [1e3, "k"] : [1, ""];
  return `${+(n / d).toFixed(unit ? 1 : 0)}${unit}`;
};

// how long from `from` to `to` (now, while it lasts), times as ms or ISO strings: 0.3s (a program takes a fraction
// of a second: not "0s"), 42s, 3m 5s
export const took = (from?: number | string | null, to?: number | string | null) => {
  if (!from) return "";
  const ms = Math.max(0, (to ? +new Date(to) : Date.now()) - +new Date(from));
  if (ms < 10_000) return `${(ms / 1000).toFixed(1)}s`;
  const s = Math.round(ms / 1000);
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
};
