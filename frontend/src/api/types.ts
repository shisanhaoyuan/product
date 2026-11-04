export type EvidenceKind = 'persisted_readback' | 'editor_snapshot' | 'source_only';
export type Fields = Record<string, unknown>;
export interface ProductCopy {
  title: string;
  description: string;
  features: string[];
}
export interface EvidenceStatus {
  evidence_kind: EvidenceKind;
  fill_complete: boolean;
  save_succeeded: boolean;
  readback_verified: boolean;
}
export interface Variant {
  sku: string;
  asin: string;
  source_url: string;
  seller: string;
  attributes: Fields;
  images: string[];
  price: string | null;
  stock: string | null;
  ean: string;
  copy: ProductCopy;
  source_facts: Fields;
  price_check: Fields;
  stock_check: Fields;
  raw_cells: Fields;
  identity_note: string;
}
export interface ProductSummary extends EvidenceStatus {
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
  issue_count: number;
}
export interface ProductDetail extends EvidenceStatus {
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
  checks: Fields;
  evidence: Fields;
  history: { id: number; observed_at: string; evidence_kind: EvidenceKind; digest: string }[];
}
export interface Stats {
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
}
