export const MAX_QUERY_LENGTH = 1500;
export const MAX_BODY_BYTES = 32 * 1024;

function normalizeString(value) {
  return typeof value === "string" ? value.trim() : "";
}

export function validateQueryPayload(payload, { maxQueryLength = MAX_QUERY_LENGTH, maxBodyBytes = MAX_BODY_BYTES } = {}) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error("request body must be a JSON object");
  }

  const query = normalizeString(payload.query);
  if (!query) {
    throw new Error("query is required");
  }

  if (query.length > maxQueryLength) {
    throw new Error(`query must be shorter than ${maxQueryLength} characters`);
  }

  const serialized = JSON.stringify(payload);
  if (serialized.length > maxBodyBytes) {
    throw new Error(`request body must be shorter than ${maxBodyBytes} bytes`);
  }

  const useRag = payload.use_rag;
  if (useRag !== undefined && typeof useRag !== "boolean") {
    throw new Error("use_rag must be a boolean");
  }

  return {
    query,
    use_rag: useRag === true,
  };
}
