// Search is plain letter matching, done in the browser against an index loaded once.
// searchKey mirrors backend/nba/textnorm.py exactly: change one, change the other.

import type { SearchEntry, SearchIndex } from "./api";

const SPECIAL: Record<string, string> = {
  đ: "d",
  ð: "d",
  ø: "o",
  ł: "l",
  ß: "ss",
  æ: "ae",
  œ: "oe",
  ı: "i",
  þ: "th",
};

/** Lowercase, strip accents and punctuation: "Nikola Jokić" -> "nikola jokic". */
export function searchKey(text: string): string {
  return text
    .toLowerCase()
    .replace(/[đðøłßæœıþ]/g, (ch) => SPECIAL[ch])
    .normalize("NFD")
    .replace(/\p{M}/gu, "")
    .replace(/-/g, " ")
    .replace(/[.'’`]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

export const MAX_RESULTS = 8;

export interface SearchGroup {
  label: "Players" | "Teams";
  entries: SearchEntry[];
}

/** 0: the name starts with the query. 1: a word in the name starts with it. 2: it appears mid-word. */
function rank(name: string, key: string): number | null {
  if (name.startsWith(key)) return 0;
  if (name.includes(` ${key}`)) return 1;
  return name.includes(key) ? 2 : null;
}

/** Best eight matches, grouped into players and teams. The group holding the best match comes first. */
export function search(index: SearchIndex, query: string): SearchGroup[] {
  const key = searchKey(query);
  if (!key) return [];

  const matches: { entry: SearchEntry; rank: number }[] = [];
  for (const entry of [...index.teams, ...index.players]) {
    const r = rank(entry.search_name, key);
    if (r !== null) matches.push({ entry, rank: r });
  }
  matches.sort((a, b) => a.rank - b.rank || a.entry.name.localeCompare(b.entry.name));
  const top = matches.slice(0, MAX_RESULTS).map((m) => m.entry);
  if (top.length === 0) return [];

  const players: SearchGroup = { label: "Players", entries: top.filter((e) => e.type === "player") };
  const teams: SearchGroup = { label: "Teams", entries: top.filter((e) => e.type === "team") };
  const ordered = top[0].type === "team" ? [teams, players] : [players, teams];
  return ordered.filter((group) => group.entries.length > 0);
}
