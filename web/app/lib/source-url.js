export function officialSourceUrl(value) {
  if (!value) return null;
  try {
    const url = new URL(value);
    if (url.protocol === "https:" && url.hostname === "laws-lois.justice.gc.ca" &&
        !url.port && !url.username && !url.password &&
        url.pathname.startsWith("/eng/regulations/SOR-96-433/")) {
      return url.href;
    }
  } catch {
    console.warn("Invalid regulation source URL.");
    return null;
  }
  console.warn("Invalid regulation source URL.");
  return null;
}
