"""Local browser acceptance; requires Playwright and installed Chrome."""
from playwright.sync_api import sync_playwright, expect
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parent
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,channel='chrome')
    page=browser.new_page(viewport={'width':1440,'height':1080},device_scale_factor=1)
    errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.goto('http://127.0.0.1:8765',wait_until='networkidle')
    page.locator('.product-card').first.wait_for()
    page.screenshot(path=str(ROOT/'desktop.png'),full_page=False)
    print(json.dumps({'cards':page.locator('.product-card').count(),'loaded_images':page.locator('img').evaluate_all('(imgs)=>imgs.filter(i=>i.complete&&i.naturalWidth>0).length'),'errors':errors},ensure_ascii=False))
    page.locator('.product-card').first.click()
    page.locator('#section-copy').wait_for()
    page.screenshot(path=str(ROOT/'detail.png'),full_page=False)
    print('Detail: '+page.locator('h1').inner_text()[:100])
    print('Variants: '+str(page.locator('[data-variant]').count()))
    listing_id=page.url.split('/product/')[1]
    data=page.request.get('http://127.0.0.1:8765/api/products/'+listing_id).json()
    for index in [0,1,len(data['variants'])-1]:
        variant=data['variants'][index]
        row=page.locator('[data-variant]').nth(index)
        row.click()
        expect(page.locator('#variant-detail .eyebrow')).to_contain_text(variant['sku'])
        if variant['copy']['title']:
            expect(page.locator('#variant-detail h3')).to_have_text(variant['copy']['title'])
        assert row.locator('td').nth(4).inner_text().split('\n')[0]==str(variant['stock'])
    page.goto('http://127.0.0.1:8765/#/products')
    page.locator('select[name="store"]').select_option('MYZ-L-22')
    expect(page.locator('#result-count')).to_contain_text('共 40 个')
    page.locator('#next').click()
    expect(page.locator('.page-number')).to_have_text('2')
    expect(page.locator('.product-card')).to_have_count(16)
    assert page.request.get('http://127.0.0.1:8765/api/export.csv?store=MYZ-L-22').status==200
    page.goto('http://127.0.0.1:8765/#/products')
    page.locator('#search').fill('B0F8QKQPVW')
    expect(page.locator('#result-count')).to_contain_text('共 1 个')
    assert page.locator('.product-card').count()==1
    page.locator('[data-view="table"]').click()
    page.locator('.table-product').wait_for()
    page.locator('[data-view="grid"]').click()
    page.goto('http://127.0.0.1:8765/#/integrations');page.locator('#import-form').wait_for()
    page.goto('http://127.0.0.1:8765/#/imports');page.locator('#import-list tbody tr').first.wait_for()
    page.goto('http://127.0.0.1:8765/docs');page.locator('.endpoint').first.wait_for()
    page.set_viewport_size({'width':390,'height':844})
    page.goto('http://127.0.0.1:8765/#/products');page.locator('.product-card').first.wait_for()
    page.screenshot(path=str(ROOT/'mobile.png'),full_page=False)
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), 'Mobile overflow'
    print('Browser checks passed; errors='+str(errors))
    assert not errors
    browser.close()
