const STORAGE_KEY = "aeroquery_user_uuid";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
let transientUuid;

export function getUserUuid() {
  let uuid;
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored && UUID.test(stored)) return stored;
    uuid = crypto.randomUUID();
    window.localStorage.setItem(STORAGE_KEY, uuid);
    return uuid;
  } catch (error) {
    console.error("Could not persist the analytics user UUID", error);
    transientUuid ??= uuid ?? crypto.randomUUID();
    return transientUuid;
  }
}
