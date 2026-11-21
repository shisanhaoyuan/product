"""Exercise the actual browser extractors against duplicate Amazon price IDs."""
import ast
from pathlib import Path
import sys
from playwright.sync_api import sync_playwright
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from catalog.source_capture import amazon_source

ASIN = 'B0HJ4NFJ4V'
def amount(value):
    return f'<span class="a-price"><span class="a-offscreen">${value}</span></span>'

def markup():
    return f'''<style>.hidden-price {{display:none}}</style><input id="ASIN" value="{ASIN}">
    <span id="glow-ingress-line2">New York 10001</span><h1 id="productTitle">Nightstand</h1>
    <div id="buyBoxAccordion">
      <div id="primeSavingsUpsellAccordionRow">Prime Member Price
        <div id="corePrice_feature_div">{amount('76.49')}</div>
      </div>
      <div id="newAccordionRow_1"><label>Regular Price</label>
        <div id="corePrice_feature_div">{amount('85.49')}</div>
        <div id="desktop_buybox"><a id="sellerProfileTriggerId">Giantex</a>
          <div id="deliveryBlockMessage">FREE delivery November 25 - 30</div>
          <div class="hidden-price"><span class="a-offscreen">$0.00</span></div>
        </div>
      </div>
    </div>
    <div id="apex_desktop">
      <div id="apex_desktop_primeSavingsUpsellAccordionRow" style="display:none">
        <div id="corePriceDisplay_desktop_feature_div">{amount('76.49')}</div>
      </div>
      <div id="apex_desktop_newAccordionRow">
        <div id="corePriceDisplay_desktop_feature_div">{amount('85.49')}
          <span class="a-price a-text-price"><span class="a-offscreen">$89.99</span></span>
        </div>
      </div>
    </div>'''

tree = ast.parse((ROOT / 'catalog/amazon_browser_worker.py').read_text())
extractor = next(n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
                 and isinstance(n.value, str) and 'function initialGallery' in n.value)
with sync_playwright() as p:
    browser = p.chromium.launch(channel='chrome', headless=True)
    context = browser.new_context()
    context.route('https://www.amazon.com/**', lambda r:r.fulfill(body=markup(),content_type='text/html'))
    page = context.new_page()
    page.goto(f'https://www.amazon.com/dp/{ASIN}')
    backend = page.evaluate(extractor)
    page.evaluate('''() => {window.submissions=[];window.chrome={runtime:{sendMessage:async d=> {
      if(d.action==='task')return {asin:'B0HJ4NFJ4V',mode:'source'};
      if(d.action==='page'){submissions.push(d);return {accepted:true};}
    }}};}''')
    page.add_script_tag(path=str(ROOT / 'browser-helper/amazon.js'))
    page.wait_for_function('submissions.length===1', timeout=12000)
    helper = page.evaluate('submissions[0].html')
    for name, html in [('worker',backend), ('helper',helper)]:
        result = amazon_source(html, ASIN)
        assert result['price']=='85.49', (name, result)
        assert result['original_price']=='89.99', (name, result)
        assert result['currency']=='USD' and result['status']=='captured', (name,result)
        assert '76.49' not in html and '$0.00' not in html
        print(f'{name}: current=85.49, original=89.99, Prime and hidden prices excluded')
    browser.close()
