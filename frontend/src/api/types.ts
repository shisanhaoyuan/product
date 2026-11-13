export type EvidenceKind = 'persisted_readback' | 'editor_snapshot' | 'source_only';
export type Fields = Record<string, unknown>;
export interface SourceCapture {
  status: 'captured' | 'partial' | 'verification_required' | 'browser_required';
  asin: string;
  source_url: string;
  captured_at?: string;
  variant_sku?: string;
  seller?: string | null;
  price?: string | null;
  original_price?: string | null;
  basis_price?: string | null;
  currency?: string | null;
  shipping?: string | null;
  delivery?: string | null;
  delivery_location?: string | null;
  offer_scope?: string;
  sync?: { filled: string[]; preserved: string[] };
  issues?: string[];
}
export interface ProductCopy {
  title: string;
  description: string;
  features: string[];
}
export interface EvidenceStatus {
  manual_review?: {
    complete: boolean;
    valid: boolean;
    at: string;
    actor: string;
    note: string;
  } | null;
  evidence_kind: EvidenceKind;
  fill_complete: boolean;
  save_succeeded: boolean;
  readback_verified: boolean;
  local_edit?: {
    revision: string;
    changed: boolean;
    updated_at: string;
    source_changed: boolean;
    conflicts: string[];
    changes: { field: string; variant_sku: string; key: string; value: unknown }[];
  };
}
export interface Variant {
  seller_origin?: 'parent' | 'direct';
  sku: string;
  asin: string;
  source_url: string;
  seller: string;
  attributes: Fields;
  images: string[];
  price: string | null;
  stock: string | null;
  ean: string;
  product_id_type: string;
  swatch_attribute: string;
  swatch_image: string;
  shipping_weight: string;
  copy: ProductCopy;
  source_facts: Fields;
  price_check: Fields;
  stock_check: Fields;
  raw_cells: Fields;
  identity_note: string;
}
export interface ProductSummary extends EvidenceStatus {
  listing_count: number;
  listing_stores: { store: string; count: number }[];
  seller_blacklist: SellerBlock[];
  category: string;
  id: string;
  asin: string;
  sku: string;
  source: string;
  source_url: string;
  seller: string;
  store: string;
  brand: string;
  template: string;
  creator: string;
  uploaded_at: string;
  observed_at: string;
  title: string;
  image: string;
  variant_count: number;
  prices: (string | null)[];
  currency?: string;
  issue_count: number;
}
export interface ProductDetail extends EvidenceStatus {
  seller_blacklist: SellerBlock[];
  category: string;
  id: string;
  external_id: string;
  asin: string;
  sku: string;
  platform_sku: string;
  source: string;
  source_url: string;
  seller: string;
  store: string;
  brand: string;
  template: string;
  record_id: string;
  creator: string;
  uploaded_at: string;
  observed_at: string;
  multiplier: string;
  copy: ProductCopy;
  images: string[];
  price: string | null;
  stock: string | null;
  msrp: string | null;
  ean: string;
  fields: Fields;
  management: Fields[];
  variants: Variant[];
  source_facts: Fields;
  issues: Fields[];
  resolved_issues?: Fields[];
  checks: Fields;
  evidence: Fields;
  history: { id: number; observed_at: string; evidence_kind: EvidenceKind; digest: string }[];
}
export interface Stats {
  unique_counts: { all_products: number; complete: number; review: number };
  listings: number;
  products: number;
  variants: number;
  saved: number;
  complete: number;
  verified: number;
  reopened: number;
  review: number;
  stores: { store: string; count: number }[];
  brands: string[];
  dates: string[];
  categories: { value: string; label: string; count: number }[];
}
export interface ProductPage {
  items: ProductSummary[];
  total: number;
  page: number;
  page_size: number;
}
export interface ImportResult {
  created: number;
  updated: number;
  unchanged: number;
  older_skipped: number;
  snapshots_added: number;
}
export interface ImportRecord {
  id: number;
  at: string;
  namespace: string;
  label: string;
  result: ImportResult;
  kind: 'folder' | 'files' | 'legacy';
  file_count: number;
  started_at: string;
  files: {
    id: number;
    at: string;
    label: string;
    namespace: string;
    path: string;
    result: ImportResult;
  }[];
}

export interface ImportContext {
  batch_id: string;
  file_id: string;
  name: string;
  kind: 'folder' | 'files';
  path: string;
}

export interface SellerBlock {
  id: number;
  source: string;
  seller: string;
  reason: string;
  active: number;
  created_at: string;
  updated_at: string;
}
export interface SellerBlockPage {
  items: SellerBlock[];
  total: number;
  page: number;
  page_size: number;
}

export interface ListingStore {
  id: number;
  name: string;
  marketplace_name: string;
  active: number;
  origin: string;
  sku_code: string | null;
}
export interface PrelistingSelection {
  listing_ids?: string[];
  variant_skus?: Record<string, string[]>;
  filters?: Record<string, string>;
}
export interface PlanInput extends PrelistingSelection {
  store_id: number;
  brand: string;
  multiplier: string;
}
export interface PricePlan {
  sku: string;
  platform_sku?: string | null;
  asin: string;
  current_price: string | null;
  stock: string | null;
  base: string | null;
  landed: string | null;
  original: string | null;
  price: string | null;
  error: string;
  content?: Pick<
    Variant,
    | 'images'
    | 'attributes'
    | 'product_id_type'
    | 'ean'
    | 'swatch_attribute'
    | 'swatch_image'
    | 'shipping_weight'
    | 'copy'
    | 'source_url'
    | 'seller'
  >;
}
export interface PrelistingPreview {
  sku_rule?: string;
  store: ListingStore;
  brand: string;
  multiplier: string;
  total: number;
  variant_count: number;
  ready_count: number;
  can_create: boolean;
  preview_hash: string;
  execution_enabled: boolean;
  items: {
    listing_id: string;
    asin: string;
    sku: string;
    title: string;
    source_store: string;
    template: string;
    observed_at: string;
    status: 'ready' | 'review';
    errors: string[];
    warnings: string[];
    msrp: string | null;
    variants: PricePlan[];
  }[];
}
export interface PrelistingBatch {
  id: string;
  status: 'draft' | 'confirmed' | 'cancelled';
  created_at: string;
  updated_at: string;
  preview: PrelistingPreview;
  execution_enabled: boolean;
}
export interface BatchPage {
  items: {
    id: string;
    status: PrelistingBatch['status'];
    created_at: string;
    store: string;
    brand: string;
    multiplier: string;
    total: number;
    ready_count: number;
    variant_count: number;
  }[];
  total: number;
  page: number;
  page_size: number;
}
