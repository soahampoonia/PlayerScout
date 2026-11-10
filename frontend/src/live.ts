// One WebSocket to /ws/live for the whole app. It reconnects by itself, tells the
// server which game and player pages are open, and hands each push to the listeners
// (LiveBridge applies them to the query cache).

import { useEffect, useSyncExternalStore } from "react";

export interface LiveMessage {
  type: string;
  data: unknown;
}

interface Subscription {
  games: string[];
  players: number[];
}

type Handler = (message: LiveMessage) => void;

class LiveSocket {
  private ws: WebSocket | null = null;
  private retry = 1000;
  private started = false;
  private subscriptions = new Map<number, Subscription>();
  private nextKey = 1;
  private handlers = new Set<Handler>();
  private statusListeners = new Set<() => void>();
  connected = false;

  start() {
    if (this.started || typeof window === "undefined") return;
    this.started = true;
    this.open();
  }

  private open() {
    const scheme = window.location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${scheme}://${window.location.host}/ws/live`);
    this.ws = ws;
    ws.onopen = () => {
      this.retry = 1000;
      this.setConnected(true);
      this.sendSubscription();
    };
    ws.onmessage = (event) => {
      let message: LiveMessage;
      try {
        message = JSON.parse(event.data as string);
      } catch {
        return;
      }
      this.handlers.forEach((handler) => handler(message));
    };
    ws.onclose = () => {
      this.ws = null;
      this.setConnected(false);
      const wait = this.retry;
      this.retry = Math.min(this.retry * 2, 30_000);
      window.setTimeout(() => this.open(), wait);
    };
    ws.onerror = () => ws.close();
  }

  private setConnected(value: boolean) {
    if (this.connected === value) return;
    this.connected = value;
    this.statusListeners.forEach((listener) => listener());
  }

  private sendSubscription() {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    const games = new Set<string>();
    const players = new Set<number>();
    this.subscriptions.forEach((sub) => {
      sub.games.forEach((g) => games.add(g));
      sub.players.forEach((p) => players.add(p));
    });
    this.ws.send(JSON.stringify({ subscribe: { games: [...games], players: [...players] } }));
  }

  subscribe(sub: Subscription): () => void {
    const key = this.nextKey++;
    this.subscriptions.set(key, sub);
    this.sendSubscription();
    return () => {
      this.subscriptions.delete(key);
      this.sendSubscription();
    };
  }

  onMessage(handler: Handler): () => void {
    this.handlers.add(handler);
    return () => this.handlers.delete(handler);
  }

  onStatus(listener: () => void): () => void {
    this.statusListeners.add(listener);
    return () => this.statusListeners.delete(listener);
  }
}

export const liveSocket = new LiveSocket();

/** True while the socket is open. Pages poll the API only when it is not. */
export function useLiveConnected(): boolean {
  return useSyncExternalStore(
    (listener) => liveSocket.onStatus(listener),
    () => liveSocket.connected,
    () => false,
  );
}

/** Ask for pushes about these games and players while the component is mounted. */
export function useLiveSubscription(games: string[], players: number[]) {
  const key = JSON.stringify([games, players]);
  useEffect(() => {
    const [g, p] = JSON.parse(key) as [string[], number[]];
    return liveSocket.subscribe({ games: g, players: p });
  }, [key]);
}
