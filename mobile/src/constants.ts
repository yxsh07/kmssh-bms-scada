/**
 * Runtime configuration for the mobile app.
 *
 * BASE_URL and SITE_ID are the ONLY place these values live.
 * No screen, component, or hook may hardcode an asset name, asset ID,
 * equipment type string, or site-specific label. Everything the app
 * displays must come from the ontology response at runtime.
 *
 * To point at a different backend or site, change these two values only.
 */

/** Backend base URL — no trailing slash. */
export const BASE_URL = 'http://localhost:3000/api/v1';

/** Site identifier passed as the :s path param. */
export const SITE_ID = 'kmssh-nas';
