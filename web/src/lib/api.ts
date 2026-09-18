/**
 * Cliente de la API. Corre SIEMPRE en el servidor.
 *
 * El navegador nunca habla con `api:8000` directo: ese nombre solo resuelve
 * dentro de la red de Docker, y cuando exista la autenticación (Etapa 4) el
 * token de sesión no tiene por qué salir del servidor.
 *
 * El contrato es doc 06 y `src/tasador/v1/reports.py`. Los tipos de acá son un
 * espejo de esa respuesta: si el backend agrega un campo, agregarlo acá; `/v1`
 * solo cambia de forma aditiva, así que nada de esto se rompe solo.
 */

import { cookies } from "next/headers";

const BASE = process.env.API_INTERNAL_URL ?? "http://api:8000";
const COOKIE_SESION = "tasador_sesion";

/**
 * El header del tenant, **solo para desarrollo**.
 *
 * Hasta el 14/08 esto era el único mecanismo y estaba hardcodeado: el front
 * decía "soy inmo-demo" y la API le creía. Ahora el camino normal es la cookie
 * de sesión; el header queda como respaldo para levantar el stack sin haber
 * creado un usuario todavía, y **la API lo ignora en producción** (ver
 * `_por_header_de_desarrollo` en `v1/auth.py`).
 */
const ORG_DE_DESARROLLO = process.env.TASADOR_ORG_SLUG ?? "";

export type Confianza = "ALTA" | "MEDIA" | "BAJA";

export type EstadoInforme =
  | "QUEUED"
  | "RUNNING"
  | "SUCCEEDED"
  | "INSUFFICIENT_DATA"
  | "FAILED"
  | "CANCELLED";

export interface Paso {
  node: string;
  status: string;
  duration_ms: number | null;
  cost_usd: number | null;
  detail: Record<string, unknown>;
}

export interface Informe {
  report_id: string;
  status: EstadoInforme;
  external_ref: string | null;
  progress: {
    current_node: string | null;
    completed: number;
    total: number;
    steps: Paso[];
  };
  cost_usd: number | null;
  methodology_version: string | null;
  prompt_bundle_version: string | null;
  generated_at?: string | null;
  valuation?: {
    currency: string;
    suggested_listing_price: { low: number | null; mid: number | null; high: number | null };
    expected_closing_range: { low: number | null; high: number | null };
    price_per_m2: number | null;
    weighted_surface: number | null;
  };
  confidence?: { level: Confianza | null; score: number | null };
  comparables?: { found: number | null; used: number | null };
  narrative_md?: string | null;
  /** El mismo markdown, ya pasado por el renderer del PDF (CommonMark sin HTML
      embebido). Ver el comentario en `reports.py`. */
  narrative_html?: string | null;
  limitations?: string[];
  insufficient_reason?: string | null;
  detail?: {
    candidates_found: number | null;
    excluded: { reason: string; count: number }[];
    suggestions: string[];
  };
  error_code?: string | null;
}

export interface ListadoInformes {
  items: {
    report_id: string;
    status: EstadoInforme;
    address: string | null;
    neighborhood: string | null;
    property_type: string | null;
    rooms: number | null;
    surface_total: number | null;
    value_mid: number | null;
    value_low: number | null;
    value_high: number | null;
    price_per_m2: number | null;
    confidence: Confianza | null;
    comparables_found: number | null;
    comparables_used: number | null;
    created_at: string;
  }[];
  next_cursor: string | null;
}

/**
 * Reenvía la sesión del navegador a la API.
 *
 * La cookie llega al servidor de Next con el request del usuario y hay que
 * pasarla explícitamente: `fetch` del lado del servidor no arrastra las
 * cookies del request entrante. Olvidarlo produce un 401 que parece un
 * problema de la API y es del front.
 */
async function credenciales(): Promise<Record<string, string>> {
  const sesion = (await cookies()).get(COOKIE_SESION)?.value;
  if (sesion) return { Cookie: `${COOKIE_SESION}=${sesion}` };
  // Sin sesión, el respaldo de desarrollo — que en producción la API rechaza.
  return ORG_DE_DESARROLLO ? { "X-Org-Slug": ORG_DE_DESARROLLO } : {};
}

async function pedir<T>(ruta: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${BASE}${ruta}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(await credenciales()),
      ...(init?.headers ?? {}),
    },
    // Un informe que está corriendo cambia cada segundo: cachear el progreso
    // sería mostrar un stepper congelado y que el usuario crea que se colgó.
    cache: "no-store",
  });
  if (!r.ok) {
    const cuerpo = await r.text().catch(() => "");
    throw new ErrorDeApi(r.status, cuerpo);
  }
  return (await r.json()) as T;
}

export class ErrorDeApi extends Error {
  constructor(
    readonly status: number,
    readonly cuerpo: string,
  ) {
    super(`La API devolvió ${status}`);
  }

  get esNoAutorizado(): boolean {
    return this.status === 401;
  }
}

export const getInforme = (id: string) => pedir<Informe>(`/v1/reports/${id}`);

export const listarInformes = (cursor?: string, limite = 25) =>
  pedir<ListadoInformes>(
    `/v1/reports?limit=${limite}` + (cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""),
  );

export const crearInforme = (body: unknown, idempotencyKey?: string) =>
  pedir<{ report_id: string; status: string; poll_url: string; estimated_seconds: number }>(
    "/v1/reports",
    {
      method: "POST",
      body: JSON.stringify(body),
      headers: idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {},
    },
  );

/**
 * El nombre del nodo, en castellano llano.
 *
 * Doc 07 §5: el usuario es un agente inmobiliario, no lee `extract_features`. Y
 * además comunica el método — que el sistema VERIFIQUE lo que escribió es parte
 * del producto y se ve acá.
 */
export const NOMBRE_DE_NODO: Record<string, string> = {
  normalize_subject: "Normalizando la dirección",
  retrieve_candidates: "Buscando comparables",
  ondemand_capture: "Capturando avisos nuevos",
  extract_features: "Analizando avisos",
  dedup_cluster: "Detectando duplicados",
  curate: "Seleccionando comparables",
  adjust_and_value: "Calculando el valor",
  market_context: "Analizando el barrio",
  write_report: "Redactando el informe",
  critic: "Verificando el informe",
  render_pdf: "Generando el PDF",
};

/** Los motivos de descarte de doc 04, en castellano. */
export const MOTIVO_DE_DESCARTE: Record<string, string> = {
  permuta_o_financiacion: "ofrecía permuta o financiación especial",
  en_pozo_o_construccion: "en pozo o en construcción",
  descripcion_inconsistente: "la descripción no cierra con los datos",
  tipologia_distinta: "es de otra tipología",
  precio_promocional: "precio promocional",
  sin_precio: "sin precio publicado",
  precio_no_usd: "publicado en pesos",
  sin_superficie: "sin superficie declarada",
  sin_direccion: "sin dirección",
  usd_m2_fuera_de_rango: "USD/m² fuera de rango plausible",
  duplicado: "duplicado de otro aviso",
  outlier_estadistico: "descartado por razones estadísticas",
};

// ── El corpus (doc 07 §8) ───────────────────────────────────────────────
export interface Features {
  condition: string | null;
  orientation: string | null;
  floor_number: number | null;
  has_elevator: boolean | null;
  age_years: number | null;
  parking_spaces: number | null;
  rooms: number | null;
  extractor_model: string | null;
  extractor_version: string | null;
  confidence: number | null;
  needs_review: boolean;
  extracted_at: string | null;
}

export interface Comparable {
  id: string;
  source: string;
  source_id: string;
  url: string | null;
  address: string | null;
  neighborhood: string | null;
  price: number | null;
  currency: string | null;
  surface_weighted: number | null;
  usd_per_m2: number | null;
  rooms: number | null;
  published_at: string | null;
  active: boolean;
  cluster_id: string | null;
  description: string | null;
  features: Features | null;
  faltantes: string[];
}

export interface ListadoComparables {
  items: Comparable[];
  next_cursor: string | null;
  total_aprox: number;
}

export const listarComparables = (params: Record<string, string | undefined>) => {
  const qs = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v) as [string, string][],
  );
  return pedir<ListadoComparables>(`/v1/comparables?${qs}`);
};

// ── Quién soy (para esconder los links de admin en el layout) ───────────
export interface Yo {
  email: string | null;
  full_name: string | null;
  role: string | null;
  org_slug: string;
  org_name: string;
  via: string;
}

/**
 * `null` si no hay sesión válida. El layout lo usa para decidir qué links
 * mostrar — la autorización real la hace la API en cada endpoint, esto es
 * solo no mostrar puertas que van a rebotar.
 */
export async function quienSoy(): Promise<Yo | null> {
  try {
    return await pedir<Yo>("/v1/auth/me");
  } catch {
    return null;
  }
}

// ── Calidad (doc 07 §9) ─────────────────────────────────────────────────
export interface Backtest {
  id: string;
  dataset: string;
  sample: number;
  seed: number;
  n_cases: number;
  n_evaluated: number;
  coverage: number | null;
  mdape: number | null;
  ppe20: number | null;
  hit_rate: number | null;
  bias: number | null;
  baseline_mdape: number | null;
  baseline_ppe20: number | null;
  por_barrio: Record<string, number>;
  por_confianza: Record<string, number>;
  engine_version: string;
  method_version: string;
  prompt_bundle_version: string;
  notes: string | null;
  created_at: string;
}

export interface CorridaDeComponente {
  componente: string;
  metrica: string;
  valor: number;
  n: number;
  objetivo: number | null;
  engine_version: string;
  prompt_bundle_version: string;
  created_at: string;
}

export interface Calidad {
  backtests: Backtest[];
  componentes: CorridaDeComponente[];
}

export const getCalidad = () => pedir<Calidad>("/v1/calidad");

// ── Fuentes (doc 07 §10) ────────────────────────────────────────────────
export interface CorridaDeIngesta {
  id: string;
  mode: string;
  status: string;
  discovered: number;
  fetched: number;
  created: number;
  updated: number;
  skipped: number;
  blocked: number;
  errors: number;
  cost_usd: number;
  started_at: string;
  finished_at: string | null;
}

export interface Fuente {
  source: string;
  corridas: number;
  ultima: CorridaDeIngesta | null;
  costo_del_mes: number;
}

export interface BarrioCobertura {
  name: string;
  activos: number;
  usables: number;
  usd_m2_mediana: number | null;
  usd_m2_oficial: number | null;
  desvio: number | null;
  estado: string;
}

export interface Fuentes {
  fuentes: Fuente[];
  barrios: BarrioCobertura[];
}

export const getFuentes = () => pedir<Fuentes>("/v1/admin/fuentes");

// ── Organización y usuarios (doc 07 §11) ────────────────────────────────
export interface ApiKeyInfo {
  id: string;
  name: string;
  prefix: string;
  last_used_at: string | null;
  revoked_at: string | null;
  created_at: string;
}

export interface Organizacion {
  name: string;
  slug: string;
  monthly_report_quota: number;
  fetch_budget_monthly: number;
  informes_del_mes: number;
  api_keys: ApiKeyInfo[];
}

export const getOrganizacion = () => pedir<Organizacion>("/v1/admin/organizacion");

export interface Usuario {
  id: string;
  email: string;
  full_name: string | null;
  role: string;
  active: boolean;
  last_login_at: string | null;
  created_at: string;
}

export const getUsuarios = () => pedir<Usuario[]>("/v1/admin/usuarios");

// ── Informe compartido (público, sin credenciales) ──────────────────────
export interface Compartido {
  address: string;
  neighborhood: string | null;
  property_type: string;
  rooms: number | null;
  surface_total: number | null;
  generated_at: string | null;
  valuation: {
    currency: string | null;
    suggested_listing_price: { low: number | null; mid: number | null; high: number | null };
    expected_closing_range: { low: number | null; high: number | null };
    price_per_m2: number | null;
  };
  confidence: { level: Confianza | null };
  narrative_md: string | null;
  limitations: string[];
}

/** SIN credenciales a propósito: el token del path es la credencial. */
export async function getCompartido(token: string): Promise<Compartido | null> {
  const r = await fetch(`${BASE}/v1/shared/${encodeURIComponent(token)}`, {
    cache: "no-store",
  });
  if (!r.ok) return null;
  return (await r.json()) as Compartido;
}

/** Los cinco campos que mueven el precio (doc 05 §4.1), en castellano. */
export const NOMBRE_DE_CAMPO: Record<string, string> = {
  condition: "Estado",
  orientation: "Orientación",
  floor_number: "Piso",
  has_elevator: "Ascensor",
  age_years: "Antigüedad",
  parking_spaces: "Cocheras",
  rooms: "Ambientes",
};
