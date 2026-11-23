"""Browser acceptance with mocked batch APIs; never generates or publishes real items."""
import json
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright, expect

BASE = os.environ.get('CATALOG_TEST_URL', 'http://127.0.0.1:8765')
OUTPUT = Path(os.environ.get('CATALOG_TEST_OUTPUT', '/tmp/jiwu-prelisting-bulk'))
OUTPUT.mkdir(parents=True, exist_ok=True)


def main():
    items = [dict(id=f'batch-{n}', status=status, created_at=f'2026-11-{24-n}T02:00:00Z',
                  store=f'测试店铺 {n}', brand='TestBrand', multiplier='1.4', total=1,
                  ready_count=1, variant_count=1, review_failed_count=0, unsubmitted_count=1)
             for n, status in [(1, 'draft'), (2, 'confirmed')]]
    generated, validated, deleted, cancel_requests = [], [], [], []
    jobs = {}
    errors = []
    queries = []

    def document(bid, validation=False):
        valid = bid == 'batch-1'
        return dict(revision=4, rows=[dict(platform_sku='SKU-' + bid)], labels={'color': '颜色'},
                    validation=dict(current=True, rows={
                        'SKU-' + bid: dict(valid=valid if validation else True,
                                          errors=[] if valid or not validation else [
                                              dict(path='visible.color', message='请选择商品颜色')])}))

    def handler(route):
        path = urlsplit(route.request.url).path
        if path == '/api/prelisting/batches':
            query = parse_qs(urlsplit(route.request.url).query)
            queries.append(query)
            filtered = items[:]
            for key, direction in [('created_from', 1), ('created_to', -1)]:
                if key in query:
                    boundary = datetime.fromisoformat(query[key][0].replace('Z', '+00:00'))
                    filtered = [item for item in filtered if
                                (datetime.fromisoformat(item['created_at'].replace('Z', '+00:00'))
                                 - boundary).total_seconds() * direction >= 0]
            return route.fulfill(json=dict(items=filtered, total=len(filtered), page=1, page_size=20))
        if path == '/api/prelisting/batches/bulk-action':
            payload = route.request.post_data_json
            deleted.append(payload)
            items.clear()
            return route.fulfill(json={'changed': len(payload['ids'])})
        if path.endswith('/action'):
            cancel_requests.append(route.request.post_data_json)
            return route.fulfill(status=500, json={'detail': 'Redundant cancellation must not be called'})
        if path.startswith('/api/prelisting/batches/') and '/walmart' in path:
            bid = path.split('/')[4]
            if path.endswith('/generation'):
                if route.request.method == 'POST':
                    generated.append(bid)
                    jobs[bid] = dict(id='job-' + bid, batch_id=bid, revision=4, state='complete',
                                     message='生成完成：1 个 SKU 可提交', report=dict(rows={}))
                    return route.fulfill(json={**jobs[bid], 'state': 'queued', 'message': '已加入生成队列'})
                return route.fulfill(json=dict(ai={'configured': True}, job=jobs.get(bid)))
            if path.endswith('/validate'):
                validated.append((bid, route.request.post_data_json))
                return route.fulfill(json=document(bid, validation=True))
            if path.endswith('/walmart'):
                return route.fulfill(json=document(bid))
            raise AssertionError('Unexpected platform operation: ' + path)
        return route.continue_()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='chrome', headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1080}, timezone_id='Asia/Shanghai')
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/api/**', handler)
        page.goto(BASE + '/#/prelisting')
        expect(page.get_by_role('heading', name='预上架批次', exact=True)).to_be_visible()
        expect(page.get_by_label('选择批次 batch-1')).not_to_be_checked()
        time_filter = page.locator('summary[aria-label^="创建时间"]')
        time_filter.click()
        page.get_by_label('创建开始时间', exact=True).fill('2026-11-23T00:00')
        page.get_by_label('创建结束时间', exact=True).fill('2026-11-23T23:59:59')
        page.screenshot(path=str(OUTPUT / 'time-desktop.png'), full_page=True)
        page.get_by_role('button', name='应用时间', exact=True).click()
        expect(page.get_by_label('选择批次 batch-2')).to_have_count(0)
        expect(page.get_by_label('选择批次 batch-1')).to_be_visible()
        assert queries[-1]['created_from'] == ['2026-11-22T16:00:00.000Z']
        assert queries[-1]['created_to'] == ['2026-11-23T15:59:59.999Z']
        time_filter.click()
        page.get_by_label('创建结束时间', exact=True).fill('2026-11-22T23:59:59')
        request_count = len(queries)
        page.get_by_role('button', name='应用时间', exact=True).click()
        expect(page.get_by_role('alert')).to_have_text('开始时间不能晚于结束时间')
        assert len(queries) == request_count
        page.get_by_role('button', name='清空', exact=True).click()
        expect(page.get_by_label('选择批次 batch-2')).to_be_visible()
        assert 'created_from' not in queries[-1] and 'created_to' not in queries[-1]
        for width in [390, 801, 1440]:
            page.set_viewport_size({'width': width, 'height': 1000})
            time_filter.click()
            panel = page.get_by_role('form', name='创建时间范围')
            bounds = panel.bounding_box()
            assert bounds and bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= width
            if width == 390:
                page.screenshot(path=str(OUTPUT / 'time-mobile.png'), full_page=True)
            page.get_by_label('创建开始时间', exact=True).press('Escape')
            expect(panel).not_to_be_visible()
        page.get_by_label('全选本页记录', exact=True).check()
        expect(page.get_by_label('选择批次 batch-1')).to_be_checked()
        expect(page.get_by_label('选择批次 batch-2')).to_be_checked()
        page.get_by_role('button', name='AI 批量填充', exact=True).click()
        expect(page.locator('.batch-preparation-state.complete')).to_have_count(2)
        assert sorted(generated) == ['batch-1', 'batch-2']
        page.get_by_role('button', name='批量审核', exact=True).click()
        expect(page.locator('.batch-preparation-state.complete')).to_have_count(1)
        expect(page.locator('.batch-preparation-state.review')).to_have_count(1)
        assert sorted(bid for bid, _ in validated) == ['batch-1', 'batch-2']
        assert all(payload == {'revision': 4} for _, payload in validated)
        page.get_by_text('查看 1 项具体原因', exact=True).click()
        expect(page.get_by_text('SKU-batch-2 · 颜色：请选择商品颜色', exact=True)).to_be_visible()
        expect(page.get_by_role('button', name='取消批次', exact=True)).to_have_count(0)
        page.screenshot(path=str(OUTPUT / 'desktop.png'), full_page=True)
        for width in [390, 801, 1440]:
            page.set_viewport_size({'width': width, 'height': 1000})
            heading = page.get_by_role('heading', name='预上架批次', exact=True).bounding_box()
            assert heading and heading['height'] < 80, f'Heading compressed at {width}'
            # Scope this regression to the batch workspace; the global navigation
            # has its own responsive acceptance and may scroll independently.
            if not page.locator('#app').evaluate('el => el.scrollWidth <= el.clientWidth && el.getBoundingClientRect().right <= innerWidth'):
                page.screenshot(path=str(OUTPUT / f'overflow-{width}.png'), full_page=True)
                outside = page.locator('#app *').evaluate_all('''elements => elements.map(el => ({
                    tag: el.tagName, cls: el.className, right: el.getBoundingClientRect().right,
                    width: el.getBoundingClientRect().width
                })).filter(el => el.right > innerWidth + 1).slice(0, 20)''')
                raise AssertionError(f'Overflow at {width}: {outside}')
        page.set_viewport_size({'width': 390, 'height': 844})
        page.evaluate('''async () => {
            window.scrollTo(0, 0);
            await new Promise(requestAnimationFrame);
            await new Promise(requestAnimationFrame);
        }''')
        page.screenshot(path=str(OUTPUT / 'mobile.png'), full_page=True)
        page.get_by_role('button', name='批量删除记录', exact=True).click()
        dialog = page.get_by_role('dialog')
        expect(dialog).to_contain_text('无需先取消批次')
        dialog.get_by_role('button', name='确认', exact=True).click()
        expect(page.locator('.empty')).to_contain_text('没有符合当前条件的批次')
        expect(page.locator('.batch-preparation')).to_have_count(0)
        assert deleted == [{'ids': ['batch-1', 'batch-2'], 'action': 'delete'}]
        assert not cancel_requests and not errors, (cancel_requests, errors)
        browser.close()
    print(json.dumps(dict(result='passed', shared_selection=2, generated=generated,
                          validated=[bid for bid, _ in validated], direct_delete=True,
                          responsive_widths=[390, 801, 1440]), ensure_ascii=False))


if __name__ == '__main__':
    main()
