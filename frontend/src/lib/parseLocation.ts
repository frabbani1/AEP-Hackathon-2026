export type LocationResult = { ok: true; value: string } | { ok: false; error: string };

const MAX_LENGTH = 60;
const ZIP = /^\d{5}(-\d{4})?$/;
const CITY = /^[\p{L}][\p{L} .'’-]*(,\s*[\p{L}][\p{L} .]*)?$/u;
const STREET_SUFFIX = /\b(street|st|avenue|ave|road|rd|drive|dr|lane|ln|boulevard|blvd|court|ct)\.?$/i;

/**
 * Validates the "where are you?" input. The product only needs a city or ZIP,
 * so anything that looks like a street address is rejected on purpose.
 */
export function parseLocation(raw: string): LocationResult {
  const value = raw.trim().replace(/\s+/g, " ");

  if (!value) {
    return { ok: false, error: "Enter a city or ZIP code." };
  }
  if (value.length > MAX_LENGTH) {
    return { ok: false, error: `Keep it under ${MAX_LENGTH} characters.` };
  }
  if (ZIP.test(value)) {
    return { ok: true, value };
  }
  if (/\d/.test(value)) {
    return {
      ok: false,
      error: "Enter a city or 5-digit ZIP code. Please don't include a street address.",
    };
  }
  if (!CITY.test(value)) {
    return { ok: false, error: "Use letters only, like Columbus or Columbus, OH." };
  }
  if (STREET_SUFFIX.test(value.split(",")[0].trim())) {
    return { ok: false, error: "That looks like a street. Enter a city or ZIP code instead." };
  }
  return { ok: true, value };
}
