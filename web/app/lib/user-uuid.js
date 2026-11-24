const STORAGE_KEY = "aeroquery.userUuid";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

export class UserUuidStorageError extends Error {}

function readOrCreateUserUuid() {
  const storage = window.localStorage;
  const stored = storage.getItem(STORAGE_KEY);
  if (stored && UUID.test(stored)) return stored;
  const uuid = crypto.randomUUID();
  if (stored !== null) storage.removeItem(STORAGE_KEY);
  storage.setItem(STORAGE_KEY, uuid);
  return uuid;
}

export function getUserUuid() {
  try {
    return readOrCreateUserUuid();
  } catch {
    try {
      return readOrCreateUserUuid();
    } catch (error) {
      console.error("Could not persist the analytics user UUID", error);
      throw new UserUuidStorageError("Could not persist the analytics user UUID");
    }
  }
}
