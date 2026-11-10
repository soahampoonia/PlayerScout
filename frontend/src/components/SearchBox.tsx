import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type SearchEntry } from "../api";
import { search } from "../search";

const path = (entry: SearchEntry) => (entry.type === "team" ? `/teams/${entry.id}` : `/players/${entry.id}`);

export function SearchBox() {
  const navigate = useNavigate();
  const listId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);

  // The whole index (30 teams, about 500 players) loads once; matching happens here.
  const index = useQuery({ queryKey: ["search-index"], queryFn: api.searchIndex, staleTime: 10 * 60_000 });
  const groups = useMemo(() => (index.data ? search(index.data, query) : []), [index.data, query]);
  const flat = useMemo(() => groups.flatMap((group) => group.entries), [groups]);

  // "/" jumps to the search box from anywhere.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing = target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable);
      if (event.key === "/" && !typing && !event.metaKey && !event.ctrlKey) {
        event.preventDefault();
        inputRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const go = (entry: SearchEntry) => {
    setQuery("");
    setOpen(false);
    inputRef.current?.blur();
    navigate(path(entry));
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (flat.length === 0) return;
      setOpen(true);
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActive((current) => (current + step + flat.length) % flat.length);
    } else if (event.key === "Enter") {
      if (open && flat[active]) go(flat[active]);
    } else if (event.key === "Escape") {
      if (open) setOpen(false);
      else setQuery("");
    }
  };

  const typed = query.trim() !== "";
  const showPanel = open && typed;
  let position = 0;

  return (
    <div className="search">
      <input
        ref={inputRef}
        type="search"
        className="search-input"
        placeholder="Search players and teams"
        aria-label="Search players and teams"
        role="combobox"
        aria-expanded={showPanel}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={showPanel && flat[active] ? `${listId}-${active}` : undefined}
        autoComplete="off"
        spellCheck={false}
        value={query}
        onChange={(event) => {
          setQuery(event.target.value);
          setActive(0);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={onKeyDown}
      />
      {!typed && (
        <kbd className="search-hint" aria-hidden="true">
          /
        </kbd>
      )}
      {showPanel && (
        <div className="search-panel" id={listId} role="listbox" aria-label="Search results">
          {flat.length === 0 && (
            <p className="search-empty">
              {index.isPending
                ? "Loading names…"
                : index.isError
                  ? "Search is unavailable: the name list did not load."
                  : `No player or team matches “${query.trim()}”.`}
            </p>
          )}
          {groups.map((group) => (
            <div key={group.label} role="group" aria-label={group.label}>
              <div className="search-group">{group.label}</div>
              {group.entries.map((entry) => {
                const i = position++;
                return (
                  <div
                    key={`${entry.type}-${entry.id}`}
                    id={`${listId}-${i}`}
                    role="option"
                    aria-selected={i === active}
                    className={i === active ? "search-option active" : "search-option"}
                    // mousedown, not click: it fires before the input's blur closes the list.
                    onMouseDown={(event) => {
                      event.preventDefault();
                      go(entry);
                    }}
                    onMouseEnter={() => setActive(i)}
                  >
                    <span className="search-name">{entry.name}</span>
                    <span className="search-meta">
                      {entry.type === "team" ? entry.team : (entry.team ?? "Free agent")}
                    </span>
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
