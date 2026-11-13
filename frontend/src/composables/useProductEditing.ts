import { inject, type InjectionKey, type Ref, type ComputedRef } from 'vue';
export interface EditTarget {
  field: string;
  variant_sku?: string;
  key?: string;
}
export interface ProductEditing {
  listingId: ComputedRef<string>;
  enabled: ComputedRef<boolean>;
  active: Ref<string>;
  busy: Ref<boolean>;
  save: (target: EditTarget, value: unknown) => Promise<void>;
  refresh?: () => Promise<void>;
}
export const productEditingKey: InjectionKey<ProductEditing> = Symbol('product-editing');
export const useProductEditing = () => inject(productEditingKey, undefined);

export const lockedAttributeKeys = new Set([
  'templetname',
  'dc_platformaccount',
  'dc_platformsku',
  'sku alias',
  'asin',
  'dc_productsourceurl',
  'dc_sourceurl',
  'record_id',
  'template',
  'sku',
  'brand name',
  'dc_price',
  'dc_initlistingquantity',
  'msrp',
  'dc_productid',
  'title',
  'description',
  'key features',
  'brand',
]);
