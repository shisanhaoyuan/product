import { createSSRApp, h } from 'vue';
import { renderToString } from '@vue/server-renderer';
import { createMemoryHistory, createRouter } from 'vue-router';
import { describe, expect, it } from 'vitest';
import ProductVariantsPreview from './ProductVariantsPreview.vue';
import ProductCard from './ProductCard.vue';
import type { ProductSummary, Variant } from '../api/types';

const product = {
  id: 'parent',
  sku: 'parent-sku',
  source: 'Amazon',
  currency: 'USD',
  seller_blacklist: [],
  listing_stores: [],
  prices: [],
  variant_count: 2,
} as unknown as ProductSummary;
const variants = [
  {
    sku: 'red-sku',
    asin: 'B000000001',
    images: ['https://example.com/red.jpg'],
    copy: { title: 'Red chair' },
    attributes: { Color: 'Red', Finish: 'Matte' },
    price: '89.99',
    stock: '0',
  },
  {
    sku: 'blue-sku',
    asin: 'B000000002',
    images: [],
    copy: { title: 'Blue chair' },
    attributes: { Color: 'Blue', Size: ['Large', 'Wide'] },
    price: null,
    stock: null,
  },
] as unknown as Variant[];

describe('search result variant display', () => {
  it('shows each child’s own image, identifiers, specifications, price and stock', async () => {
    const html = await renderToString(
      createSSRApp(ProductVariantsPreview, {
        product,
        state: { expanded: true, loading: false, error: '', variants },
      }),
    );
    for (const value of [
      'red-sku',
      'blue-sku',
      'B000000001',
      'B000000002',
      'Red',
      'Matte',
      'Blue',
      'Large / Wide',
      '$89.99',
      '库存 · 0',
      '库存 · 未记录',
    ])
      expect(html).toContain(value);
    expect(html.match(/src="https:\/\/example.com\/red.jpg"/g)).toHaveLength(1);
    expect(html).toContain('variant-preview-no-stock');
    expect(html).toContain('暂无商品图片');
  });

  it('shows loading, retryable errors and empty results', async () => {
    const render = (state: { loading: boolean; error: string; variants?: Variant[] }) =>
      renderToString(
        createSSRApp(ProductVariantsPreview, { product, state: { expanded: true, ...state } }),
      );
    expect(await render({ loading: true, error: '' })).toContain('正在加载子变体');
    const failed = await render({ loading: false, error: '网络错误' });
    expect(failed).toContain('网络错误');
    expect(failed).toContain('重新加载');
    expect(await render({ loading: false, error: '', variants: [] })).toContain('暂无子变体记录');
  });

  it('displays ERP-wrapped colors as readable text and omits temporary fields', async () => {
    const html = await renderToString(
      createSSRApp(ProductVariantsPreview, {
        product,
        state: {
          expanded: true,
          loading: false,
          error: '',
          variants: [
            {
              ...variants[0]!,
              attributes: { Color: { val: ['White'] }, TMP_CHANGE_ATTR_KEY: { val: [''] } },
            },
          ],
        },
      }),
    );
    expect(html).toContain('颜色：');
    expect(html).toContain('White');
    expect(html).not.toContain('&quot;val&quot;');
    expect(html).not.toContain('TMP_CHANGE_ATTR_KEY');
  });

  it('keeps the expansion action outside the product card navigation link', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/:pathMatch(.*)*', component: { render: () => null } }],
    });
    await router.push('/products');
    const app = createSSRApp({
      render: () => h(ProductCard, { product }, { variants: () => h('button', '展开子变体') }),
    });
    app.use(router);
    const html = await renderToString(app);
    expect(html).toContain('product-card-link');
    expect(html.indexOf('</a>')).toBeLessThan(html.indexOf('<button'));
  });
});
