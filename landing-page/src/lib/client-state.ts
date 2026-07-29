/**
 * Page-level "which client am I looking at" state.
 *
 * The hero's client toggle used to only swap the hero's own shells. It now
 * drives every chat surface on the page - the hero shells, the features
 * frames, and the connect widget's selected target - so picking ChatGPT once
 * makes the whole page speak ChatGPT.
 *
 * The state lives in one place, `<html data-client>`, because that lets CSS do
 * the switching: `:root[data-client="goose"]` restyles every frame at first
 * paint, with no per-client markup duplicated per surface and no flash while
 * module scripts load. This module owns writes, persistence and notification;
 * readers can use `onClient` or just style off the attribute.
 */
export const CLIENT_IDS = ["claude", "chatgpt", "goose", "vscode"] as const;
export type ClientId = (typeof CLIENT_IDS)[number];

export const DEFAULT_CLIENT: ClientId = "claude";

const STORAGE_KEY = "daysurface:client";
const EVENT = "daysurface:client";

export function isClientId(value: unknown): value is ClientId {
  return CLIENT_IDS.includes(value as ClientId);
}

export function getClient(): ClientId {
  const current = document.documentElement.dataset.client;
  return isClientId(current) ? current : DEFAULT_CLIENT;
}

export function setClient(id: string): void {
  if (!isClientId(id) || id === getClient()) return;
  document.documentElement.dataset.client = id;
  try {
    localStorage.setItem(STORAGE_KEY, id);
  } catch {
    // Private mode / storage disabled: the choice just won't outlive the page.
  }
  document.dispatchEvent(new CustomEvent<ClientId>(EVENT, { detail: id }));
}

/** Run `cb` for the current client now, and again on every change. */
export function onClient(cb: (id: ClientId) => void): void {
  cb(getClient());
  document.addEventListener(EVENT, (e) => cb((e as CustomEvent<ClientId>).detail));
}
