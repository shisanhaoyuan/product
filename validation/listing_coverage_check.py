"""Read-only browser checks; duplicate-store coverage is injected into API responses."""
import os
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


BASE = os.environ.get('CATALOG_TEST_URL', 'http://127.0.0.1:8767')
ROOT = Path(__file__).resolve().parent


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, channel='chrome')
        page = browser.new_page(viewport={'width': 1682, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        response = page.request.get(BASE + '/api/products?deduplicate=true')
        assert response.ok
        data = response.json()
        assert len({row['asin'] for row in data['items']}) == len(data['items'])
        for row in data['items']:
            assert row['listing_count'] == sum(entry['count'] for entry in row['listing_stores'])
        page.add_init_script("localStorage.setItem('catalog-view','table')")
        page.goto(BASE + '/#/products')
        expect(page.locator('.listing-coverage-cell')).to_have_count(len(data['items']))
        first = page.locator('.listing-coverage-cell').first
        expect(first).to_contain_text(f"上架 {data['items'][0]['listing_count']} 次")
        headers = page.locator('.data-table th').all_text_contents()
        assert headers.index('商品') < headers.index('上架次数 / 店铺') < headers.index('ASIN / SKU')
        page.screenshot(path=str(ROOT / 'asin-coverage-desktop.png'))

        fixture = data['items'][0] | {'listing_count': 5, 'listing_stores': [
            {'store': 'MYZ-L-21', 'count': 3}, {'store': 'MYZ-L-22', 'count': 2}]}

        def products(route):
            assert 'deduplicate=true' in route.request.url
            route.fulfill(json=dict(items=[fixture], total=1, page=1, page_size=24))

        page.route('**/api/products?*', products)
        page.reload()
        expect(page.locator('.listing-coverage-cell')).to_have_count(1)
        expect(first).to_contain_text('上架 5 次')
        expect(first).to_contain_text('MYZ-L-21 · 3 次')
        expect(first).to_contain_text('MYZ-L-22 · 2 次')
        page.screenshot(path=str(ROOT / 'asin-coverage-multiple-stores.png'))
        page.locator('[data-view="grid"]').click()
        expect(page.locator('.product-card .listing-coverage')).to_contain_text('上架 5 次')
        for width in (320, 390, 801, 1024, 1920):
            page.set_viewport_size({'width': width, 'height': 900})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
        page.set_viewport_size({'width': 390, 'height': 844})
        page.screenshot(path=str(ROOT / 'asin-coverage-mobile.png'), full_page=True)
        page.locator('input[type=checkbox]').last.check()
        page.get_by_role('button', name='预上架', exact=True).click()
        expect(page.locator('.prelisting-dialog')).to_be_visible()
        assert not errors, errors
        print('Passed: ASIN coverage, middle column, multi-store counts, grid/table, selection and responsive layouts.')
        browser.close()


if __name__ == '__main__':
    main()
