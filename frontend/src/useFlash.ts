import { useEffect, useRef, useState } from "react";

/** True for a moment after `value` changes (not on first render). */
export function useFlash(value: unknown, ms = 1400): boolean {
  const previous = useRef(value);
  const [flash, setFlash] = useState(false);
  useEffect(() => {
    if (Object.is(previous.current, value)) return;
    previous.current = value;
    setFlash(true);
    const timer = window.setTimeout(() => setFlash(false), ms);
    return () => window.clearTimeout(timer);
  }, [value, ms]);
  return flash;
}
