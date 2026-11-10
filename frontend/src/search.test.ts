import { describe, expect, it } from "vitest";
import type { SearchIndex } from "./api";
import { search, searchKey } from "./search";

describe("searchKey", () => {
  // The same cases as backend/nba/tests/test_ingest.py::test_search_key.
  it.each([
    ["Nikola Jokić", "nikola jokic"],
    ["Luka Dončić", "luka doncic"],
    ["Shai Gilgeous-Alexander", "shai gilgeous alexander"],
    ["P.J. Tucker", "pj tucker"],
    ["De'Aaron Fox", "deaaron fox"],
    ["Nikola Đurišić", "nikola durisic"],
    ["  LeBron   JAMES ", "lebron james"],
  ])("%s -> %s", (name, key) => {
    expect(searchKey(name)).toBe(key);
  });
});

function entry(type: "team" | "player", id: number, name: string, team: string | null, extra = "") {
  return { type, id, name, team, search_name: searchKey(`${name} ${extra}`) };
}

const index: SearchIndex = {
  teams: [
    entry("team", 1, "Los Angeles Lakers", "LAL", "LAL"),
    entry("team", 2, "Boston Celtics", "BOS", "BOS"),
    entry("team", 3, "Dallas Mavericks", "DAL", "DAL"),
  ],
  players: [
    entry("player", 10, "LeBron James", "LAL"),
    entry("player", 11, "James Harden", "LAC"),
    entry("player", 12, "Luka Dončić", "LAL"),
    entry("player", 13, "Jalen Brown", "BOS"),
    entry("player", 14, "Jaylen Brown", "BOS"),
    entry("player", 15, "Nikola Jokić", "DEN"),
    entry("player", 16, "Shai Gilgeous-Alexander", "OKC"),
    entry("player", 17, "Dalano Banton", null),
  ],
};

const names = (query: string) => search(index, query).flatMap((group) => group.entries.map((e) => e.name));

describe("search", () => {
  it("ignores case and accents", () => {
    expect(names("JOKIC")).toEqual(["Nikola Jokić"]);
    expect(names("dončić")).toEqual(["Luka Dončić"]);
    expect(names("gilgeous-alex")).toEqual(["Shai Gilgeous-Alexander"]);
  });

  it("puts names that start with the query first, then word starts, then the rest", () => {
    // "James Harden" starts with it; "LeBron James" has a word that starts with it.
    expect(names("james")).toEqual(["James Harden", "LeBron James"]);
    // Mid-word matches still show, last.
    expect(names("ron")).toEqual(["LeBron James"]);
  });

  it("finds teams by city, nickname or abbreviation and groups them", () => {
    expect(names("lal")).toEqual(["Los Angeles Lakers"]);
    const groups = search(index, "dal");
    expect(groups.map((g) => g.label)).toEqual(["Players", "Teams"]);
    expect(groups[0].entries[0].name).toBe("Dalano Banton");
    expect(groups[1].entries[0].name).toBe("Dallas Mavericks");
  });

  it("returns nothing for an empty or unmatched query and caps the list at eight", () => {
    expect(search(index, "   ")).toEqual([]);
    expect(search(index, "zzz")).toEqual([]);
    expect(names("a").length).toBe(8);
  });
});
