import { OntologyResponse } from 'shared';
import { BASE_URL } from '../constants';

/**
 * Fetch the full ontology for a given site.
 *
 * @param siteId  - Runtime site identifier (never hardcoded at call site).
 * @returns       - Typed OntologyResponse containing all assets and their points.
 * @throws        - Re-throws network / HTTP errors so callers can surface them.
 */
export async function fetchOntology(siteId: string): Promise<OntologyResponse> {
  const url = `${BASE_URL}/sites/${siteId}/ontology`;
  const response = await fetch(url);

  if (!response.ok) {
    throw new Error(
      `Ontology fetch failed: ${response.status} ${response.statusText} (${url})`
    );
  }

  return (await response.json()) as OntologyResponse;
}
