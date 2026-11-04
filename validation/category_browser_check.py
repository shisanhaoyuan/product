"""Read-only category filter acceptance on the running local catalog."""
import csv
import io
from pathlib import Path
from urllib.parse import urlencode
from playwright.sync_api import sync_playwright, expect

BASE = 'http://127.0.0.1:8765'
ROOT = Path(__file__).resolve().parent
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, channel='chrome')
    page = browser.new_page(viewport={'width': 1440, 'height': 1080})
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto(BASE + '/#/products?page=2')
    select = page.get_by_label('筛选商品类目')
    expect(page.locator('.page-number')).to_have_text('2')
    stats = page.request.get(BASE + '/api/stats').json()
    category = next(c for c in stats['categories'] if c['label'] == 'Cat Trees (猫树)')
    select.select_option(category['value'])
    expect(page.locator('#result-count')).to_contain_text(f"共 {category['count']} 个")
    expect(page.locator('.page-number')).to_have_text('1')
    expect(page.locator('.product-card')).to_have_count(category['count'])
    assert set(page.locator('.product-card .category-label').all_text_contents()) == {category['label']}
    page.reload()
    expect(select).to_have_value(category['value'])
    page.get_by_label('筛选店铺').select_option('MYZ-L-21')
    query = urlencode({'category': category['value'], 'store': 'MYZ-L-21'})
    expected = page.request.get(BASE + '/api/products?' + query).json()
    expect(page.locator('#result-count')).to_contain_text(f"共 {expected['total']} 个")
    export = page.locator('#export-button').get_attribute('href')
    csv_rows = list(csv.DictReader(io.StringIO(page.request.get(BASE + export).text().lstrip('\ufeff'))))
    assert len(csv_rows) == expected['total']
    assert all(row['商品类目'] == category['label'] and row['店铺'] == 'MYZ-L-21' for row in csv_rows)
    page.get_by_label('筛选店铺').select_option('')
    expect(page.locator('.product-card')).to_have_count(category['count'])
    page.screenshot(path=str(ROOT / 'category-filter.png'))
    page.locator('[data-view="table"]').click()
    assert set(page.locator('.table-product .category-label').all_text_contents()) == {category['label']}
    page.locator('.table-product').first.click()
    expect(page.locator('.identity-item').filter(has_text='商品类目（已记录 Product Type）')).to_contain_text(category['label'])
    page.go_back()
    expect(select).to_have_value(category['value'])
    page.locator('#reset').click()
    expect(select).to_have_value('')
    expect(page.locator('#result-count')).to_contain_text(f"共 {stats['listings']} 个")
    page.locator('[data-view="grid"]').click()
    for width in [390, 801]:
        page.set_viewport_size({'width': width, 'height': 900})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), width
    assert not errors, errors
    print(f"Category checks passed: {len(stats['categories'])} categories; counts, page reset, refresh, combined filters, CSV, detail, back, reset, mobile.")
    browser.close()
