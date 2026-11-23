const STORAGE_KEY = "aeroquery_user_uuid";
const LEGACY_STORAGE_KEY = "aeroquery_user_key";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
let transientUuid;

export function getUserUuid() {
  let uuid;
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored && UUID.test(stored)) return stored;
    const legacy = window.localStorage.getItem(LEGACY_STORAGE_KEY);
    uuid = legacy && UUID.test(legacy) ? legacy : crypto.randomUUID();
    window.localStorage.setItem(STORAGE_KEY, uuid);
    if (legacy !== null) window.localStorage.removeItem(LEGACY_STORAGE_KEY);
    return uuid;
  } catch (error) {
    console.error("Could not persist the analytics user UUID", error);
    transientUuid ??= uuid ?? crypto.randomUUID();
    return transientUuid;
  }
}
