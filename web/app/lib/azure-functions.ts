export function getAzureFunctionConfig() {
  const apiUrl = process.env.AZURE_FUNCTION_API_URL || process.env.API_URL;
  const functionKey = process.env.AZURE_FUNCTION_KEY;

  if (!apiUrl || !functionKey) return null;

  return {
    apiUrl: apiUrl.replace(/\/+$/, ""),
    functionKey,
  };
}

export function getClientIp(request: Request) {
  return request.headers.get("x-nf-client-connection-ip")?.trim() || "unknown";
}
