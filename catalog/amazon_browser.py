"""Optional isolated Wraith fallback for public Amazon product pages."""
import json
import os
import re
import signal
import subprocess
import tempfile
import threading
import logging
from pathlib import Path

from .images import HumanVerificationRequired

_GATE = threading.BoundedSemaphore(1)
MAX_RESULT = 7 * 1024 * 1024


class BrowserHTML(str):
    def __new__(cls, html, report):
        value = super().__new__(cls, html)
        value.wraith_test = report
        return value


def test_summary(report):
    count = report.get('attempts', 0)
    if report.get('challenge_cleared'):
        state = '验证后已进入同 ASIN 商品页'
    elif report.get('challenge_seen'):
        state = '仍未通过验证' if report.get('status') == 'verification_required' else '验证尝试已停止，商品页未核验'
    else:
        state = '浏览器中未观察到人机验证'
    return f'Wraith 测试：{count}/3 轮；{state}'


def configured():
    return bool(os.environ.get('CATALOG_AMAZON_BROWSER_PYTHON', '').strip())


def fetch_page(asin):
    if not re.fullmatch(r'[A-Z0-9]{10}', asin):
        raise ValueError('缺少有效 ASIN')
    executable = os.environ.get('CATALOG_AMAZON_BROWSER_PYTHON', '').strip()
    if not executable or not Path(executable).is_file():
        raise HumanVerificationRequired('后台浏览器未安装或路径无效，请按部署说明配置 Wraith')
    engine = os.environ.get('CATALOG_AMAZON_BROWSER_ENGINE', 'camoufox')
    if engine not in ('camoufox', 'chromium'):
        raise HumanVerificationRequired('后台浏览器引擎配置无效')
    # Workers never read a person's Chrome profile, credentials, or cookie files.
    if not _GATE.acquire(timeout=1):
        raise HumanVerificationRequired('后台浏览器正在处理其他商品，请稍后重试价格采集')
    try:
        with tempfile.TemporaryDirectory(prefix='jiwu-amazon-') as folder:
            output = Path(folder) / 'result.json'
            command = [executable, str(Path(__file__).with_name('amazon_browser_worker.py')),
                       asin, engine, str(output)]
            env = {key: os.environ[key] for key in ('PATH','HOME','LANG','LC_ALL','TMPDIR')
                   if key in os.environ}
            if cache := os.environ.get('CATALOG_AMAZON_BROWSER_CACHE'):
                env['XDG_CACHE_HOME'] = cache
                env['HOME'] = str(Path(cache).parent)
                env['CATALOG_AMAZON_BROWSER_PROFILE'] = str(Path(cache).parent / 'profile')
            if profile := os.environ.get('CATALOG_AMAZON_BROWSER_PROFILE'):
                env['CATALOG_AMAZON_BROWSER_PROFILE'] = profile
            process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       env=env, start_new_session=(os.name == 'posix'))
            try:
                process.wait(timeout=240)
            except subprocess.TimeoutExpired:
                raise HumanVerificationRequired('后台浏览器采集超时，尚未取得有效报价') from None
            finally:
                # Close the worker's entire process group, including an unresponsive browser.
                if os.name == 'posix':
                    try: os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                elif process.poll() is None:
                    process.kill()
                process.wait()
            if not output.exists() or output.stat().st_size > MAX_RESULT:
                raise HumanVerificationRequired('后台浏览器未返回有效结果，请检查运行环境')
            try:
                result = json.loads(output.read_text())
            except (ValueError, OSError):
                raise HumanVerificationRequired('后台浏览器结果无法读取') from None
            messages = {
                'browser_busy': '后台浏览器会话正在使用中，请稍后重试',
                'unavailable': 'Wraith 或浏览器依赖未就绪，请检查后台浏览器安装',
                'verification_required': 'Wraith 后台尝试后仍有人机验证，需要人工完成验证或核对原价',
                'rate_limited': '来源网站限制了服务器访问，后台浏览器未能恢复报价',
                'identity_mismatch': '后台浏览器返回了其他商品页面，未采用报价',
                'location_unverified': '后台浏览器未能确认配送地区 10001，未采用报价',
                'failed': '后台浏览器采集失败，未取得可核验的报价',
            }
            raw = result.get('wraith_test') or {}
            report = {key: raw[key] for key in ('attempts','challenge_seen','challenge_cleared',
                      'product_verified','elapsed_seconds','status','location_verified') if key in raw}
            report['message'] = test_summary(report)
            logging.getLogger(__name__).info('Wraith ASIN %s: %s', asin, json.dumps(report, ensure_ascii=False))
            if result.get('status') != 'ok' or not isinstance(result.get('html'), str):
                error = HumanVerificationRequired(report['message'] + '；' + messages.get(result.get('status'), messages['failed']))
                error.wraith_test = report
                raise error
            return BrowserHTML(result['html'], report)
    except OSError:
        raise HumanVerificationRequired('后台浏览器无法启动，请检查运行权限和依赖') from None
    finally:
        _GATE.release()
