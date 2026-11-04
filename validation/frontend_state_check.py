"""Browser regression checks with intercepted responses; never writes catalog data."""
import asyncio
import copy
import re
from pathlib import Path

from playwright.async_api import async_playwright, expect

BASE = 'http://127.0.0.1:8765'
ROOT = Path(__file__).resolve().parent


async def main():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, channel='chrome')
        page = await browser.new_page(viewport={'width': 1440, 'height': 1080})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        response = await page.request.get(BASE + '/api/products')
        summaries = (await response.json())['items']
        first_id, second_id = [row['id'] for row in summaries[:2]]
        first = await (await page.request.get(BASE + '/api/products/' + first_id)).json()
        second = await (await page.request.get(BASE + '/api/products/' + second_id)).json()

        # Complete a slow old request after navigating to another product.
        requested, release = asyncio.Event(), asyncio.Event()

        async def slow_product(route):
            requested.set()
            await release.wait()
            await route.fulfill(json=first)

        await page.route('**/api/products/' + first_id, slow_product)
        await page.goto(BASE + '/#/product/' + first_id, wait_until='domcontentloaded')
        await asyncio.wait_for(requested.wait(), timeout=10)
        await page.goto(BASE + '/#/product/' + second_id)
        await expect(page.locator('h1')).to_have_text(second['copy']['title'])
        release.set()
        await page.wait_for_load_state('networkidle')
        await expect(page.locator('h1')).to_have_text(second['copy']['title'])
        await expect(page.locator('#variant-detail .eyebrow')).to_contain_text(second['variants'][0]['sku'])
        await page.unroute('**/api/products/' + first_id, slow_product)

        # Different values, copy and order must stay attached to their own SKU.
        fixture = copy.deepcopy(first)
        fixture['variants'] = list(reversed(fixture['variants'][:2]))
        zero, unknown = fixture['variants']
        zero.update(price='0', stock='0', ean='TEST-EAN-ZERO')
        zero['copy'] = {'title': 'Zero SKU', 'features': ['Only zero SKU feature'], 'description': 'Only zero SKU description'}
        unknown.update(price=None, stock=None, ean='TEST-EAN-UNKNOWN')
        unknown['copy'] = {'title': 'Unknown SKU', 'features': ['Only unknown SKU feature'], 'description': 'Only unknown SKU description'}

        async def variant_fixture(route):
            await route.fulfill(json=fixture)

        await page.route('**/api/products/' + first_id, variant_fixture)
        await page.goto(BASE + '/#/product/' + first_id)
        await expect(page.locator('[data-variant]')).to_have_count(2)
        for index, variant in enumerate(fixture['variants']):
            row = page.locator('[data-variant]').nth(index)
            assert await row.get_attribute('data-variant') == variant['sku']
            await row.click()
            await expect(row.locator('td').nth(3).locator('.price')).to_have_text('$0.00' if index == 0 else '—')
            assert (await row.locator('td').nth(4).inner_text()).split('\n')[0] == ('0' if index == 0 else '—')
            await expect(row.locator('td').nth(5)).to_have_text(variant['ean'])
            await expect(page.locator('#variant-detail h3')).to_have_text(variant['copy']['title'])
            await expect(page.locator('#variant-detail .features')).to_have_text(variant['copy']['features'][0])
            await expect(page.locator('#variant-detail .description')).to_have_text(variant['copy']['description'])
        await page.unroute('**/api/products/' + first_id, variant_fixture)

        failures = 0

        async def fail_once(route):
            nonlocal failures
            if failures == 0:
                failures += 1
                await route.fulfill(status=503, json={'detail': 'Temporary catalog failure'})
            else:
                await route.continue_()

        pattern = re.compile(r'/api/products\?')
        await page.route(pattern, fail_once)
        await page.goto(BASE + '/#/products')
        await expect(page.locator('.error-box')).to_contain_text('Temporary catalog failure')
        await page.get_by_role('button', name='重试', exact=True).click()
        await expect(page.locator('.product-card')).to_have_count(24)
        await page.unroute(pattern, fail_once)
        await page.locator('#search').fill('B0F8QKQPVW')
        await expect(page.locator('#result-count')).to_contain_text('共 1 个')
        await expect(page.locator('#search')).to_be_focused()
        await page.locator('#reset').click()
        await expect(page.locator('.product-card')).to_have_count(24)

        for width in [320, 801, 1024, 1920]:
            await page.set_viewport_size({'width': width, 'height': 900})
            assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'Overflow at {width}'
            boxes = await page.locator('.top-nav a').evaluate_all('(links) => links.map(a => ({x: a.getBoundingClientRect().x, right: a.getBoundingClientRect().right}))')
            assert all(a['right'] <= b['x'] + 1 for a, b in zip(boxes, boxes[1:])), f'Navigation overlap at {width}'
        await page.set_viewport_size({'width': 1440, 'height': 1080})
        await page.locator('[data-view="table"]').click()
        await page.screenshot(path=str(ROOT / 'table.png'))
        assert not errors, errors
        print('Passed: stale requests, SKU-specific zero/unknown values and copy, HTTP failure recovery, search focus, 320/801/1024/1920px layouts.')
        await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
