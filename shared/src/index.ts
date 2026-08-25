// ---------------------------------------------------------------------------
// Legacy types (backend compat – do not remove)
// ---------------------------------------------------------------------------
export interface Point {
  id: string;
  name: string;
  value: number;
  timestamp: string;
  unit?: string;
}

export interface Asset {
  id: string;
  name: string;
  points: Point[];
  status: 'active' | 'inactive' | 'error';
}

// ---------------------------------------------------------------------------
// Ontology-driven types (PROJECT_CONTEXT.md §2 – 13 point classifications)
// ---------------------------------------------------------------------------

/** Exhaustive list of the 13 point classifications. Never switch on asset type
 *  or asset name — always switch on this field. */
export type PointCls =
  | 'Temperature Sensor'
  | 'Humidity Sensor'
  | 'Pressure Sensor'
  | 'Flow Sensor'
  | 'Power/Energy Meter'
  | 'Status/Control'
  | 'Alarm/Fault'
  | 'Actuator/Position'
  | 'Setpoint'
  | 'Mode/Control'
  | 'Calculated KPI'
  | 'Count'
  | 'Other';

/** A single measurable/controllable point on an asset, as returned by the
 *  ontology endpoint. All fields come from the runtime ontology — nothing
 *  is hardcoded in the app. */
export interface OntologyPoint {
  /** Unique point identifier (string, may contain site prefix). */
  id: string;
  /** Human-readable label sourced from the ontology. */
  name: string;
  /** Classification — drives PointRenderer, never the asset type. */
  cls: PointCls;
  /** Engineering unit, e.g. "°C", "kW", "%RH". May be absent. */
  unit?: string;
  /** Writable flag — true when this point accepts setpoint/mode writes. */
  writable?: boolean;
}

/** An asset node as returned by GET /api/v1/sites/:s/ontology. */
export interface OntologyAsset {
  /** Unique asset identifier (string, includes site prefix). */
  id: string;
  /** Human-readable name sourced from the ontology. */
  name: string;
  /** Equipment type label (e.g. "Chiller", "AHU") — sourced from ontology,
   *  NEVER used for rendering decisions inside PointRenderer. */
  type: string;
  /** Zone / location string sourced from the ontology. */
  zone?: string;
  /** Ordered list of points belonging to this asset. */
  points: OntologyPoint[];
}

/** Top-level response shape for GET /api/v1/sites/:s/ontology. */
export interface OntologyResponse {
  siteId: string;
  assets: OntologyAsset[];
}
